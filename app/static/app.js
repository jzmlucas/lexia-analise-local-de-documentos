const $ = id => document.getElementById(id);
const selected = new Map();
let running = false;

function safe(s){return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function show(id){ $(id).classList.remove('hidden'); }
function hide(id){ $(id).classList.add('hidden'); }
function fileId(file){return `${file.name}-${file.size}-${file.lastModified}`;}
function appendFiles(files){
  for (const f of files) {
    if(!/\.(pdf|docx|txt|md)$/i.test(f.name)) continue;
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
    $('ollamaStatus').textContent=body.ollama ? `● Ollama conectado · ${body.models.length} modelo(s) disponível(is)` : `⚠ Ollama indisponível: ${body.error||'verifique instalação'}`;
  }catch(e){ $('ollamaStatus').textContent='⚠ Não foi possível consultar Ollama.'; }
}
$('refreshModels').onclick=health;
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
async function run(){
  if(running || selected.size===0) return;
  const arr=[...selected.values()];
  if(arr.length>40||arr.some(f=>f.size>30*1024*1024)||arr.reduce((a,f)=>a+f.size,0)>120*1024*1024){
    show('error');$('error').textContent='Limite: 40 arquivos, 30 MB cada e 120 MB no total.';return;
  }
  running=true;renderFiles();$('startBtn').textContent='Análise em andamento…';progress('Carregando documentos',2);
  try{
    const body=new FormData();arr.forEach(f=>body.append('files',f));body.append('model',$('model').value);body.append('enable_ocr',$('ocr').checked?'true':'false');
    const res=await fetch('/api/jobs',{method:'POST',body});
    if(!res.ok)throw new Error((await res.text()).slice(0,250));
    const job=await res.json();
    while(true){
      await new Promise(resolve=>setTimeout(resolve,1100));
      const resp=await fetch(`/api/jobs/${job.id}`);if(!resp.ok)throw new Error('A análise expirou ou não foi encontrada.');
      const d=await resp.json();
      if(d.status==='done'){done(d.result,job.id);break;}
      if(d.status==='error')throw new Error(d.error||'Falha ao analisar documentos');
      progress(d.message,d.progress);
    }
  }catch(e){hide('progressPanel');hide('result');show('error');$('error').textContent=e.message;}
  finally{running=false;$('startBtn').innerHTML='Iniciar nova análise jurídica <span>→</span>';renderFiles();}
}
$('startBtn').onclick=run;
health();
