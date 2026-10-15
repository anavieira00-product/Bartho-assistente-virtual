const slides=[...document.querySelectorAll('.slide')];let i=0;
function show(n){i=Math.max(0,Math.min(slides.length-1,n));slides.forEach((s,j)=>s.classList.toggle('active',j===i));document.getElementById('counter').textContent=String(i+1).padStart(2,'0')+' / '+String(slides.length).padStart(2,'0');document.getElementById('progress').style.width=((i+1)/slides.length*100)+'%'}
document.getElementById('prev').onclick=()=>show(i-1);document.getElementById('next').onclick=()=>show(i+1);
document.getElementById('full').onclick=()=>document.fullscreenElement?document.exitFullscreen():document.documentElement.requestFullscreen?.();
document.addEventListener('keydown',e=>{
 const el=e.target;
 const digitando=!!(el && (
   el.isContentEditable ||
   ['INPUT','TEXTAREA','SELECT'].includes(el.tagName) ||
   el.closest?.('#demoForm, .inputrow, #chat')
 ));
 if(digitando) return;
 if(['ArrowRight',' ','PageDown'].includes(e.key)){e.preventDefault();show(i+1)}
 if(['ArrowLeft','PageUp'].includes(e.key)){e.preventDefault();show(i-1)}
 if(e.key==='Home')show(0);
 if(e.key==='End')show(slides.length-1);
});
const API_URL='https://bartho-assistente-virtual.onrender.com/mensagem';
const CONVERSA_ID_KEY='bartho_conversa_id';
let CONVERSA_ID=localStorage.getItem(CONVERSA_ID_KEY);
if(!CONVERSA_ID){CONVERSA_ID=(window.crypto && crypto.randomUUID) ? crypto.randomUUID() : ('bartho-'+Date.now()+'-'+Math.random().toString(36).slice(2));localStorage.setItem(CONVERSA_ID_KEY,CONVERSA_ID);}
const chat=document.getElementById('chat');
let sending=false;
function bubble(t,c){let d=document.createElement('div');d.className='bubble '+c;d.textContent=t;chat.appendChild(d);chat.scrollTop=chat.scrollHeight;return d}
async function sendToBartho(text){
 if(sending)return;
 sending=true;
 const sendBtn=document.getElementById('sendBtn');
 if(sendBtn)sendBtn.disabled=true;
 const pending=bubble('Consultando o Bartho…','bot');
 try{
  const response=await fetch(API_URL,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mensagem:text,conversa_id:CONVERSA_ID})});
  const data=await response.json().catch(()=>({}));
  pending.remove();
  if(!response.ok)throw new Error(data.erro||`A API retornou HTTP ${response.status}.`);
  if(!data.resposta)throw new Error('A API respondeu, mas não retornou o campo "resposta".');
  bubble(data.resposta,'bot');
 }catch(err){
  pending.remove();
  bubble('Não consegui concluir a solicitação ao Bartho. Verifique a conexão com o serviço. Detalhe: '+err.message,'bot');
 }finally{sending=false;if(sendBtn)sendBtn.disabled=false;}
}
document.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>{const t=b.dataset.action;bubble(t,'user');sendToBartho(t)});
document.getElementById('demoForm').onsubmit=e=>{e.preventDefault();const input=document.getElementById('msg'),t=input.value.trim();if(!t||sending)return;input.value='';bubble(t,'user');sendToBartho(t)};
show(0);
