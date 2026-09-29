"""Local jobs with cancellation, subprocess timeout and optimistic commit."""
import copy
import hashlib
import json
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from .agent_schema import AgentRequest, Limits
from .agent_runner import write_json

ACTIVE={}
GUARD=threading.RLock()


def fingerprint(project):
    return hashlib.sha256(json.dumps(project,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def start(project,directory,request,commit):
    request=AgentRequest.model_validate(request)
    limits=Limits.environment()
    with GUARD:
        if project['id'] in ACTIVE:raise ValueError('Já existe uma análise em andamento neste projeto.')
        jobid=str(uuid.uuid4());folder=Path(directory)/'agent_jobs'/jobid;folder.mkdir(parents=True)
        write_json(folder/'input.json',{'project':project,'request':request.model_dump(),'limits':limits.model_dump()})
        state={'id':jobid,'status':'queued','stage':'diagnostics','events':[],'limits':limits.model_dump()}
        write_json(folder/'status.json',state)
        record={'id':jobid,'folder':folder,'cancelled':False,'process':None}
        ACTIVE[project['id']]=record
        def worker():
            started=time.monotonic()
            try:
                with (folder/'worker.log').open('w',encoding='utf8') as output:
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)
                    process=subprocess.Popen([sys.executable,'-m','backend.agent_worker',str(folder)],
                                             cwd=Path(__file__).resolve().parents[1],stdout=output,stderr=output,
                                             creationflags=creationflags)
                    record['process']=process;state['status']='running';write_json(folder/'status.json',state)
                    while process.poll() is None:
                        if record['cancelled'] or time.monotonic()-started>limits.max_seconds:
                            process.terminate();process.wait(timeout=10)
                            state['status']='cancelled' if record['cancelled'] else 'timed_out'
                            state['error']='Execução cancelada; projeto preservado.' if record['cancelled'] else 'Limite de tempo atingido; projeto preservado.'
                            break
                        time.sleep(.15)
                    else:
                        if process.returncode!=0:
                            state.update(status='failed',error='Ferramenta falhou; projeto preservado. Consulte o relatório da execução.')
                        elif record['cancelled']:state['status']='cancelled'
                        else:
                            result=json.loads((folder/'result.json').read_text(encoding='utf8'))
                            if commit(result,fingerprint(project)):
                                state['status']=result['agent_report']['status']
                            else:
                                state.update(status='conflict',error='Projeto revisado durante a análise. Resultado preservado para consulta, sem sobrescrever correções.')
                state['elapsed_seconds']=round(time.monotonic()-started,2)
                if not (folder/'report.json').exists():
                    partial=json.loads((folder/'progress.json').read_text(encoding='utf8')) if (folder/'progress.json').exists() else {}
                    write_json(folder/'report.json',dict(partial,status=state['status'],error=state.get('error'),limits=limits.model_dump()))
            except Exception as exc:
                state.update(status='failed',error=f'Não foi possível executar o agente: {type(exc).__name__}. Projeto preservado.')
            finally:
                write_json(folder/'status.json',state)
                with GUARD:ACTIVE.pop(project['id'],None)
        threading.Thread(target=worker,daemon=True).start()
        return state


def get(directory,jobid):
    try:uuid.UUID(jobid)
    except ValueError:raise ValueError('Execução inválida.')
    folder=Path(directory)/'agent_jobs'/jobid
    if not (folder/'status.json').exists():raise ValueError('Execução não encontrada.')
    state=json.loads((folder/'status.json').read_text(encoding='utf8'))
    if state['status'] in ('queued','running') and Path(directory).name not in ACTIVE:
        state.update(status='interrupted',error='Servidor reiniciado durante a análise; projeto preservado. Inicie nova execução.')
        write_json(folder/'status.json',state)
    if (folder/'progress.json').exists():
        progress=json.loads((folder/'progress.json').read_text(encoding='utf8'))
        state={**progress,**{k:v for k,v in state.items() if k not in ('events','stage')}}
    return state


def cancel(pid,jobid):
    with GUARD:
        record=ACTIVE.get(pid)
        if not record or record['id']!=jobid:raise ValueError('Execução não está ativa.')
        record['cancelled']=True
    return {'status':'cancelling','id':jobid}
