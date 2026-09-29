"""Human transcriptions tied to exact source hashes, not a generic OCR result.

Positions are pixels in the supplied JPGs. They are reviewable evidence.
The low-resolution second table must be checked against a higher-resolution original.
"""
PROFILES={
'77caaf3092b3a9554c7069c0dc6cbd84e847b328cbbf86166aeac05c8c95f81f': {
 'title':'Distribuidora de gás', 'crs':'EPSG:4674','area':90.,'perimeter':42.,
 'bbox':[1075,431,1240,525], 'crs_bbox':[900,560,1100,605],
 'vertices':[['V1',-37.951931,-7.015041],['V2',-37.951982,-7.015024],['V3',-37.951935,-7.014897],['V4',-37.951883,-7.014914]],
 'page_ring':[[431,779],[204,698],[414,121],[645,204]],
 'control_indices':[0,1,2,3],
 'notes':['Transcrição humana da tabela de 4 vértices, vinculada ao SHA-256.',
          'Cantos da planta indicados manualmente; confira a sobreposição.',
          'Datum SIRGAS 2000 legível. Área e perímetro declarados: 90 m² e 42 m.'],
},
'05d661ddad76aea751ea1a5a87b0728f88903dcaef58ec7ea92d7bf1e8c217e0':{
 'title':'Gleba I • Setor Central', 'crs':'EPSG:31983','area':314480.,'perimeter':2500.87,
 'bbox':[687,59,994,524], 'crs_bbox':[37,640,144,676],
 'vertices':[
 ['M46',238033.861,9363836.100],['M45',238018.124,9363795.494],
 ['M44',238045.227,9363785.469],['M43',238079.265,9363770.191],
 ['M42',238083.779,9363735.610],['M41',238110.046,9363698.117],
 ['M40',238153.867,9363698.507],['M39',238199.810,9363707.684],
 ['M38',238252.469,9363662.493],['M37',238285.183,9363637.580],
 ['M36',238312.452,9363603.166],['M35',238363.145,9363594.527],
 ['M34',238399.906,9363578.668],['M33',238435.031,9363567.672],
 ['M66',238450.798,9363416.890],['M65',238360.723,9363407.867],
 ['M64',238369.860,9363360.145],['M63',238374.889,9363317.338],
 ['M62',238367.893,9363272.896],['M61',238358.066,9363233.960],
 ['M60',238224.262,9363264.011],['M59',238092.758,9363293.278],
 ['M58',238075.359,9363236.679],['M57',237942.577,9363301.480],
 ['M56',237832.843,9363321.707],['M55',237703.026,9363349.430],
 ['M54',237713.314,9363491.830],['M53',237722.343,9363610.765],
 ['M52',237648.117,9363662.151],['M51',237690.057,9363707.507],
 ['M50',237718.983,9363752.645],['M49',237801.139,9363780.998],
 ['M48',237935.904,9363823.373],['M47',237985.964,9363833.925]],
 'page_ring':None,
 'controls':[
   {'index':0,'page':[343,72]}, {'index':13,'page':[617,255]},
   {'index':14,'page':[627,356]}, {'index':19,'page':[565,479]},
   {'index':25,'page':[120,400]}, {'index':28,'page':[87,189]},
   {'index':32,'page':[279,78]}],
 'notes':['Transcrição humana de 34 linhas; imagem de baixa resolução. Conferência obrigatória.',
          'SIRGAS 2000 / UTM 23S informado no carimbo; eixos impressos da grade são inconsistentes.',
          'Possíveis erros na tabela: conferir M59, M53 e distâncias antes de aceitar.',
          'Controles visuais aproximados; resíduos de ajuste não são acurácia de levantamento.'],
}}
PDF_HASH='e8e8bb6deb6228847e7cb125b301b48dbda61b9fcd00864b7e6f6dc2ef2ff5f3'
