"""Bounded state machine with real tools and an optional model planner."""
import copy
import json
import time
from collections import Counter
from pathlib import Path
from shapely.geometry import shape, box
from .agent_schema import AgentRequest, Limits, normalize_feature, object_type
from .agent_provider import Provider, ProviderUnavailable
from .document_tools import DocumentTools
from .examples import PDF_HASH

REASONS={
    'diagnostics':'Identificar páginas, textos, vetores e imagens para escolher ferramentas.',
    'embedded':'Verificar metadados GeoPDF antes de ajustar controles.',
    'native_text':'Ler textos com caixas, orientação e ancoragem originais.',
    'ocr':'Texto nativo insuficiente: executar OCR local.',
    'regions':'Separar mapa principal, legenda, tabelas, carimbo e localização.',
    'vectors':'Extrair caminhos e símbolos nativos, excluindo regiões auxiliares.',
    'raster':'Imagens presentes: detectar linhas, símbolos e regiões candidatas.',
    'legend':'Relacionar textos da legenda a símbolos e estilos; propor classes.',
    'spatial':'Localizar evidências de CRS, grade e tabelas de coordenadas.',
    'georeference':'Escolher metadados utilizáveis antes de controles verificados.',
    'reconstruct':'Acionar adaptadores SIG verificados quando a fonte for reconhecida.',
    'annotations':'Preservar textos do mapa como objetos espaciais editáveis.',
    'roads':'Recuperar superfícies viárias quando a legenda e o detector sustentarem a classe.',
    'classify':'Confrontar candidatos com legenda e evidências; manter ambiguidades.',
    'associate':'Associar textos por contenção única; expor vínculos ambíguos.',
    'validate':'Validar geometria e aplicar somente a referência espacial comprovada.',
}


def write_json(path, value):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False),encoding='utf8');tmp.replace(path)


def run(project,directory,request=None,limits=None,progress=None):
    request=AgentRequest.model_validate(request or {})
    limits=Limits.model_validate(limits) if limits else Limits.environment()
    start=time.monotonic();deadline=start+limits.max_seconds
    provider=Provider(limits,deadline) if request.mode=='ai' else None
    ctx=DocumentTools(project,directory,request,limits,deadline,provider)
    status='completed';stop_reason=None; calls=0; successful=set()
    def emit(stage,event):
        state={'status':'running','stage':stage,'page':ctx.current,'elapsed_seconds':round(time.monotonic()-start,2),
               'tool_calls':calls,'events':ctx.logs,'model_calls':provider.calls if provider else 0,
               'estimated_cost_usd':provider.reserved_usd if provider else 0}
        if progress:progress(state)
    def execute(name,reason):
        nonlocal calls
        ctx.checkpoint()
        if calls>=limits.max_tools:raise TimeoutError('Limite de ferramentas atingido.')
        calls+=1;began=time.monotonic()
        entry={'index':calls,'tool':name,'page':ctx.current,'reason':reason,'status':'running'}
        ctx.logs.append(entry);emit(name,'started')
        result=getattr(ctx,name)()
        successful.add((ctx.current,name))
        entry.update(status='completed',duration_seconds=round(time.monotonic()-began,3),
                     result={k:v if not isinstance(v,(list,dict)) else {'count':len(v)} for k,v in result.items()})
        emit(name,'completed')
    try:
        execute('diagnostics',REASONS['diagnostics'])
        for page in ctx.pages:
            ctx.current=page
            while True:
                allowed=ctx.available()
                if not allowed:break
                name=allowed[0];reason=REASONS[name]
                if ctx.cloud_enabled and provider and len(allowed)>1:
                    try:
                        choice=provider.choose(allowed,ctx.summary());name=choice['tool'];reason=choice['reason']
                    except ProviderUnavailable as exc:
                        ctx.issues.append(str(exc));ctx.cloud_enabled=False
                try:execute(name,reason)
                except (ValueError,RuntimeError) as exc:
                    if ctx.logs[-1]['status']=='running':ctx.logs[-1].update(status='failed',error=str(exc))
                    ctx.issues.append(f'Página {page}, ferramenta {name}: {exc}')
                    if name in ('embedded','regions','georeference'):
                        # Fail closed: never skip spatial prerequisites.
                        raise
                ctx.done[page].add(name)
    except (TimeoutError,RuntimeError,ValueError) as exc:
        status='partial';stop_reason=str(exc);ctx.issues.append(stop_reason)
    if any(e['status']=='failed' for e in ctx.logs):status='partial'
    # Anything produced after the last validate stays in page coordinates.
    for page in ctx.pages:
        if (page,'validate') not in successful:
            for f in ctx.generated:
                if f['page']==page:f.update(geometry=None,area=None,perimeter=None)
    final=ctx.project
    existing={f['id']:normalize_feature(copy.deepcopy(f)) for f in ctx.original
              if not (status=='completed' and f['page'] in ctx.pages and f.get('extraction_method','legacy_profile') not in ('legacy_profile','verified_profile_adapter')
                      and f['status']=='candidate' and f['revision']==0)}
    for f in ctx.generated:
        if f['id'] in existing and (existing[f['id']]['status']!='candidate' or existing[f['id']]['revision']>0):continue
        existing[f['id']]=f
    final['features']=list(existing.values())
    final['classes']=list(ctx.classes.values())
    final['regions']={str(i):s['regions'] for i,s in ctx.pages.items()}
    final['issues']=list(dict.fromkeys(final['issues']+ctx.issues))
    final['issues']=[('Processamento anterior: '+i) if i.startswith('Vias e construções foram reconhecidas') else i for i in final['issues']]
    final['rejected_candidates'].extend(ctx.rejected)
    final['stage']='extracted'
    if any(s['truncated'] for s in ctx.pages.values()):
        final['issues'].append('Limite de candidatos atingido em uma ferramenta; cobertura parcial explicitada no relatório.')
    final['diagnostic'].update(generated=len(final['features']),rejected=len(final['rejected_candidates']),
                                pending=sum(f['status']=='candidate' for f in final['features']))
    baseline=ctx.original or (ctx.baseline or {}).get('features',[])
    def in_same_region(f):
        s=ctx.pages.get(f['page'])
        if not s:return False
        p=shape(normalize_feature(copy.deepcopy(f))['page_geometry']).representative_point()
        return any(r['role']=='main_map' and box(*r['bbox']).covers(p) for r in s['regions']) and not any(
            r['role']!='main_map' and r['confirmed'] and box(*r['bbox']).covers(p) for r in s['regions'])
    def counts(fs):
        return {'objects':len(fs),'classes':dict(Counter(f['category'] for f in fs)),
                'types':dict(Counter(object_type(f) for f in fs))}
    comparisons={}
    for page in ctx.pages:
        before=[f for f in baseline if f['page']==page and in_same_region(f)]
        after=[f for f in final['features'] if f['page']==page and in_same_region(f)]
        comparisons[str(page)]={'regions':ctx.pages[page]['regions'],'baseline':counts(before),'agent':counts(after),
                                'profile_adapter_objects':sum(f['extraction_method']=='verified_profile_adapter' for f in after),
                                'note':'Mesmas regiões; contagem de candidatos não mede acurácia ou melhora de classificação.'}
    issues_by_type={
        'unidentified':[f['id'] for f in final['features'] if f['category']=='nao_identificado'],
        'ambiguous_text_links':[f['id'] for f in final['features'] if f['attributes'].get('link_candidates')],
        'invalid_geometries':[f['id'] for f in final['features'] if not shape(f['page_geometry']).is_valid],
        'without_georeferencing':[f['id'] for f in final['features'] if not f['geometry']],
    }
    lots=[f for f in baseline if f['category']=='lote'];claims=Counter((f.get('quadra'),f.get('lote')) for f in lots if f.get('quadra') and f.get('lote'))
    label_info={}
    if project['sha256']==PDF_HASH:
        from .pdf_lots import labels
        with ctx.pdf() as doc:
            native_lots,native_blocks=labels(doc[0])
        label_info={'native_lot_labels':len(native_lots),'native_block_labels':len(native_blocks),
                    'repeated_block_numbers':dict(Counter(x[0] for x in native_blocks)),
                    'missing_lot_geometries_relative_to_labels':len(native_lots)-len(lots),
                    'block_warning':'18 rótulos de quadra correspondem a 11 números distintos; há números repetidos por registro/matrícula. As 19 partes geométricas não equivalem a 19 quadras únicas.'}
    report={'status':status,'stop_reason':stop_reason,'mode_requested':request.mode,
            'model_used':bool(provider and any(x['status']=='completed' for x in provider.usage)),
            'model':provider.model if provider else None,'provider':provider.provider if provider else None,
            'source':project['name'],'sha256':project['sha256'],'limits':limits.model_dump(),
            'elapsed_seconds':round(time.monotonic()-start,3),'tool_calls':calls,'tools':ctx.logs,
            'provider_calls':provider.usage if provider else [],'estimated_cost_usd':provider.reserved_usd if provider else 0,
            'cost_note':'Estimativa pelas tarifas configuradas; verifique a cobrança no provedor.',
            'comparison':comparisons,'quality':issues_by_type,'issues':final['issues'],
            'georeferencing':final['georeferencing'],'external_positional_error_m':None,
            'classification_error_rate':None,'text_link_error_rate':None,
            'evaluation_note':'Sem verdade de campo ou amostra revisada, taxas de erro não foram estimadas.',
            'legacy_findings':{**label_info,'lots':len(lots),'quadra_parts':sum(f['category']=='quadra' for f in baseline),
                               'unique_quadra_labels':len({f.get('quadra') for f in lots if f.get('quadra')}),
                               'duplicate_lot_claims':[{'quadra':q,'lote':l,'count':n} for (q,l),n in claims.items() if n>1],
                               'lots_with_area_mismatch':sum(f.get('area_check',{}).get('ok') is False for f in lots),
                               'note':'Motor anterior escolhe variantes pela área, agrupa quadras por união e descartava a máscara de vias. Área coincidente não prova limites nem quadra.'},
            'truncated_pages':[i for i,s in ctx.pages.items() if s['truncated']], 'statistics':ctx.stats}
    final['agent_report']=report
    write_json(Path(directory)/'agent-report.json',report)
    return final
