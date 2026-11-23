const $ = id => document.getElementById(id);
const selected = new Map();
let running = false;

function safe(s){return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function show(id){ $(id).classList.remove('hidden'); }
function hide(id){ $(id).classList.add('hidden'); }
function fileId(file){return `${file.name}-${file.size}-${file.lastModified}`;}
function appendFiles(files){
  for (const f of files) {
    if(!/\.(pdf|docx|xlsx|csv|png|jpg|jpeg|tif|tiff|txt|md)$/i.test(f.name)) continue;
    selected.set(fileId(f), f);
  }
  renderFiles();
}
function renderFiles(){
  $('fileList').replaceChildren();
  for (const [key, file] of selected.entries()) {
    const row = document.createElement('div'); row.className='file';
    const icon=document.createElement('span');icon.className='file-icon';icon.textContent='◈';
    const name=document.createElement('span');name.className='file-name';name.textContent=`${file.name} · ${(file.size/1024/1024).toFixed(2)} MB`;
    const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.title='Remover';remove.disabled=running;
    remove.addEventListener('click',()=>{selected.delete(key);renderFiles();});
    row.append(icon,name,remove);$('fileList').append(row);
  }
  $('startBtn').disabled=running || selected.size===0;
}
const zone=$('dropzone');
zone.onclick=()=>$('fileInput').click();
zone.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('fileInput').click();}};
zone.ondragover=e=>{e.preventDefault();zone.classList.add('drag');};
zone.ondragleave=e=>{e.preventDefault();zone.classList.remove('drag');};
zone.ondrop=e=>{e.preventDefault();zone.classList.remove('drag');appendFiles(e.dataTransfer.files);};
$('fileInput').onchange=e=>{appendFiles(e.target.files);e.target.value='';};

async function health(){
  try {
    const r=await fetch('/api/health'); const body=await r.json();
    const options=body.models?.length ? body.models : [body.default_model||'qwen3.5:9b'];
    $('model').replaceChildren();
    for (const m of options) {
      const opt=document.createElement('option');opt.value=m;opt.textContent=m;
      if(m===body.default_model) opt.selected=true;
      $('model').append(opt);
    }
    $('ollamaStatus').textContent=body.provider!=='ollama'
      ? (body.configured ? `● ${body.provider} configurado · ${body.default_model}` : `⚠ Chave de ${body.provider} ausente no servidor`)
      : (body.ollama ? `● Ollama conectado · ${body.models.length} modelo(s) disponível(is)` : `⚠ Ollama indisponível: ${body.error||'verifique instalação'}`);
  }catch(e){ $('ollamaStatus').textContent='⚠ Não foi possível consultar Ollama.'; }
}
$('refreshModels').onclick=health;
async function loadCases(){
  const r=await fetch('/api/cases');if(!r.ok)return;
  const current=$('caseSelect').value;
  $('caseSelect').replaceChildren(new Option('Análise avulsa',''));
  for(const item of await r.json())$('caseSelect').add(new Option(item.name,item.id));
  $('caseSelect').value=current;
}
$('createCase').onclick=async()=>{
  const name=$('caseName').value.trim();if(!name)return;
  const body=new FormData();body.append('name',name);
  const r=await fetch('/api/cases',{method:'POST',body});
  if(!r.ok){alert('Não foi possível criar o caso.');return;}
  const item=await r.json();await loadCases();$('caseSelect').value=item.id;$('caseName').value='';
};
function progress(message, value){
  show('progressPanel');hide('empty');hide('error');hide('result');
  $('progressText').textContent=message;
  $('progressFill').style.width=`${Math.max(0, Math.min(value,100))}%`;
  $('progressPercent').textContent=`${value}%`;
}
function done(data,id){
  hide('progressPanel');show('result');
  $('numFiles').textContent=data.documentos;
  $('numChunks').textContent=`${data.trechos_analisados}/${data.trechos_totais}`;
  $('numEvidence').textContent=data.evidencias_verificadas;
  $('wordLink').href=`/api/jobs/${id}/download/docx`;
  $('jsonLink').href=`/api/jobs/${id}/download/json`;
  $('warnings').replaceChildren();
  for (const warning of [...data.avisos, ...data.falhas.slice(0,5)]) {
    const div=document.createElement('div');div.className='warning';div.textContent=warning;$('warnings').append(div);
  }
  $('sections').replaceChildren();
  for (const section of data.secoes) {
    const details=document.createElement('details');details.className='section-preview';
    const summary=document.createElement('summary');summary.textContent=`${section.nome} · ${section.pontos.length} ponto(s)`;
    const list=document.createElement('ul');
    for (const point of section.pontos.slice(0,12)) {
      const li=document.createElement('li');li.textContent=point.texto;
      const ref=document.createElement('small');ref.textContent=`Fontes: ${point.fontes.join(', ')} · ${point.natureza}`;
      li.append(ref);list.append(li);
    }
    details.append(summary,list);$('sections').append(details);
  }
}
function liveEvent(event){
  if(event.type==='evidence' && event.evidence?.length){
    $('sections').replaceChildren();
    const details=document.createElement('details');details.className='section-preview';details.open=true;
    const summary=document.createElement('summary');summary.textContent=`Evidências verificadas · ${event.total}`;
    const list=document.createElement('ul');
    for(const item of event.evidence){const li=document.createElement('li');li.textContent=`${item.titulo}: ${item.descricao}`;const ref=document.createElement('small');ref.textContent=`Fonte: ${item.arquivo}, ${item.localizacao} · citação literal verificada`;li.append(ref);list.append(li);}
    details.append(summary,list);$('sections').append(details);show('result');
    $('numEvidence').textContent=event.total;
  }else if(event.type==='section' && event.section){
    let details=[...$('sections').querySelectorAll('.section-preview')].find(node=>node.dataset.section===event.section.nome);
    if(!details){details=document.createElement('details');details.className='section-preview';details.dataset.section=event.section.nome;details.open=true;const summary=document.createElement('summary');details.append(summary,document.createElement('ul'));$('sections').append(details);show('result');}
    details.querySelector('summary').textContent=`${event.section.nome} · ${event.section.pontos.length} ponto(s)`;
    const list=details.querySelector('ul');list.replaceChildren();for(const point of event.section.pontos){const li=document.createElement('li');li.textContent=point.texto;const ref=document.createElement('small');ref.textContent=`Fontes: ${point.fontes.join(', ')} · ${point.natureza}`;li.append(ref);list.append(li);}
  }
}
async function run(){
  if(running || selected.size===0) return;
  const arr=[...selected.values()];
  if(arr.length>40||arr.some(f=>f.size>30*1024*1024)||arr.reduce((a,f)=>a+f.size,0)>120*1024*1024){
    show('error');$('error').textContent='Limite: 40 arquivos, 30 MB cada e 120 MB no total.';return;
  }
  running=true;renderFiles();$('startBtn').textContent='Análise em andamento…';progress('Carregando documentos',2);
  try{
    const body=new FormData();arr.forEach(f=>body.append('files',f));body.append('model',$('model').value);body.append('enable_ocr',$('ocr').checked?'true':'false');body.append('case_id',$('caseSelect').value);
    const res=await fetch('/api/jobs',{method:'POST',body});
    if(!res.ok)throw new Error((await res.text()).slice(0,250));
    const job=await res.json();
    const source=new EventSource(`/api/jobs/${job.id}/events`);
    source.onmessage=event=>{try{liveEvent(JSON.parse(event.data));}catch{}};
    while(true){
      await new Promise(resolve=>setTimeout(resolve,800));
      const resp=await fetch(`/api/jobs/${job.id}`);if(!resp.ok)throw new Error('A análise expirou ou não foi encontrada.');
      const d=await resp.json();if(d.events?.length)liveEvent(d.events[d.events.length-1]);
      if(d.status==='done'){source.close();done(d.result,job.id);break;}
      if(d.status==='error'){source.close();throw new Error(`${d.error||'Não foi possível concluir a análise.'}${d.error_id?` (código ${d.error_id})`:''}`);}
      progress(d.message,d.progress);if(d.events?.length)show('result');
    }
  }catch(e){hide('progressPanel');hide('result');show('error');$('error').textContent=e.message;}
  finally{running=false;$('startBtn').innerHTML='Iniciar nova análise jurídica <span>→</span>';renderFiles();}
}
$('startBtn').onclick=run;
health();
loadCases();

const libraryMessage = message => { $('libraryMessage').textContent = message; };
$('libraryToggle').onclick=async()=>{
  const open=$('libraryToggle').getAttribute('aria-expanded')!=='true';
  $('libraryToggle').setAttribute('aria-expanded',String(open));
  $('libraryBody').classList.toggle('hidden',!open);
  $('libraryToggle').textContent=open?'Fechar biblioteca':'Adicionar documentos';
  if(open)await loadLibrary();
};
async function loadLibrary(){
  const response=await fetch('/api/library');
  if(!response.ok){libraryMessage('Não foi possível carregar a biblioteca.');return;}
  const data=await response.json();
  $('libraryList').replaceChildren();
  if(!data.documents.length){$('libraryList').textContent='Nenhum documento de referência adicionado.';return;}
  for(const item of data.documents){
    const row=document.createElement('div');row.className='library-item';
    const info=document.createElement('div');
    const name=document.createElement('strong');name.textContent=item.name;
    const meta=document.createElement('small');meta.textContent=`${data.categories[item.category]} · ${item.chunks} trecho(s) · ${item.territory||'Território não informado'}`;
    info.append(name,meta);
    const actions=document.createElement('div');actions.className='library-actions';
    const link=document.createElement('a');link.href=`/api/library/${item.category}/${item.id}/download`;link.textContent='Original';
    const remove=document.createElement('button');remove.type='button';remove.textContent='Excluir';
    remove.onclick=async()=>{
      if(!confirm(`Excluir "${item.name}" da biblioteca?`))return;
      const result=await fetch(`/api/library/${item.category}/${item.id}`,{method:'DELETE'});
      libraryMessage(result.ok?'Documento excluído.':'Não foi possível excluir.');await loadLibrary();
    };
    actions.append(link,remove);row.append(info,actions);$('libraryList').append(row);
  }
}
$('libraryUpload').onclick=async()=>{
  const files=[...$('libraryFiles').files];
  if(!files.length){libraryMessage('Selecione ao menos um documento.');return;}
  const total=files.reduce((size,file)=>size+file.size,0);
  if(files.length>40||files.some(file=>file.size>30*1024*1024)||total>120*1024*1024){
    libraryMessage('Limite: 40 arquivos, 30 MB cada e 120 MB por envio.');return;
  }
  const body=new FormData();for(const file of files)body.append('files',file);
  body.append('category',$('libraryCategory').value);
  body.append('source_url',$('librarySource').value);
  body.append('territory',$('libraryTerritory').value);
  body.append('effective_date',$('libraryEffective').value);
  body.append('enable_ocr','true');
  $('libraryUpload').disabled=true;libraryMessage('Extraindo e organizando documentos…');
  try{
    const response=await fetch('/api/library',{method:'POST',body});
    const result=await response.json();
    if(!response.ok)throw new Error('Não foi possível adicionar os documentos. Verifique os arquivos e tente novamente.');
    libraryMessage(`${result.documents.length} documento(s) adicionado(s).`+(result.failures.length?` Não foi possível ler ${result.failures.length} arquivo(s).`:''));
    $('libraryFiles').value='';await loadLibrary();
  }catch(error){libraryMessage(String(error.message||error));}
  finally{$('libraryUpload').disabled=false;}
};
$('librarySearch').onclick=async()=>{
  const q=$('libraryQuery').value.trim();if(!q)return;
  const response=await fetch('/api/library/search?q='+encodeURIComponent(q));
  $('libraryResults').replaceChildren();
  if(!response.ok){libraryMessage('Falha na busca.');return;}
  const {results}=await response.json();
  if(!results.length){$('libraryResults').textContent='Nenhum trecho encontrado.';return;}
  for(const item of results){
    const entry=document.createElement('div');entry.className='library-hit';
    const title=document.createElement('strong');title.textContent=`${item.name} · ${item.location}`;
    const quote=document.createElement('p');quote.textContent=item.excerpt.slice(0,320);
    entry.append(title,quote);$('libraryResults').append(entry);
  }
};
$('libraryQuery').onkeydown=event=>{if(event.key==='Enter')$('librarySearch').click();};
