import { readFileSync, writeFileSync, mkdirSync } from 'fs';
import { createHash } from 'crypto';
import { performance } from 'perf_hooks';

const SPEC = '2026-07-28';
const TIMEOUT_MS = 8000;
const CONCURRENCY = 4;
const MODE = process.env.MODE || 'sentinel';
const WAVE_ID = process.env.WAVE_ID || new Date().toISOString().replace(/[:.]/g,'-');
const RUN_ID = process.env.GITHUB_RUN_ID || '';
const RUN_ATTEMPT = process.env.GITHUB_RUN_ATTEMPT || '';
const EVENT = process.env.GITHUB_EVENT_NAME || '';

function parseCsvLine(line) {
  const out=[]; let cur=''; let q=false;
  for (let i=0;i<line.length;i++) {
    const ch=line[i];
    if (q) {
      if (ch === '"' && line[i+1] === '"') { cur+='"'; i++; }
      else if (ch === '"') q=false;
      else cur+=ch;
    } else {
      if (ch === '"') q=true;
      else if (ch === ',') { out.push(cur); cur=''; }
      else cur+=ch;
    }
  }
  out.push(cur); return out;
}
function loadPanel(path) {
  const lines=readFileSync(path,'utf8').trim().split(/\r?\n/);
  const hdr=parseCsvLine(lines.shift());
  return lines.map(line=>{
    const vals=parseCsvLine(line); const o={};
    hdr.forEach((h,i)=>o[h]=vals[i]??'');
    o.sentinel=o.sentinel==='1'; o.pilot_block=Number(o.pilot_block);
    return o;
  });
}
function stableStringify(x) {
  if (x === null || typeof x !== 'object') return JSON.stringify(x);
  if (Array.isArray(x)) return '['+x.map(stableStringify).join(',')+']';
  return '{'+Object.keys(x).sort().map(k=>JSON.stringify(k)+':'+stableStringify(x[k])).join(',')+'}';
}
const sha = x => createHash('sha256').update(typeof x==='string'?x:stableStringify(x)).digest('hex');
function seededShuffle(a, seed) {
  let h=createHash('sha256').update(seed).digest();
  let s=0n; for (let i=0;i<8;i++) s=(s<<8n)+BigInt(h[i]);
  function rnd(){ s ^= s<<13n; s ^= s>>7n; s ^= s<<17n; return Number(s & ((1n<<53n)-1n))/2**53; }
  const b=[...a];
  for(let i=b.length-1;i>0;i--){ const j=Math.floor(rnd()*(i+1)); [b[i],b[j]]=[b[j],b[i]]; }
  return b;
}
function meta() {
  return {
    'io.modelcontextprotocol/protocolVersion': SPEC,
    'io.modelcontextprotocol/clientInfo': {name:'risu-readiness-longitudinal',version:'1.0'},
    'io.modelcontextprotocol/clientCapabilities': {}
  };
}
function extractResult(text, ctype) {
  let obj=null;
  try {
    if ((ctype||'').includes('text/event-stream')) {
      for(const line of text.split(/\r?\n/)) if(line.startsWith('data:')) {
        try { obj=JSON.parse(line.slice(5).trim()); break; } catch {}
      }
    } else obj=JSON.parse(text);
  } catch {}
  return obj;
}
async function rpc(url, method) {
  const ctl=new AbortController();
  const timer=setTimeout(()=>ctl.abort(), TIMEOUT_MS);
  const body={jsonrpc:'2.0',id:1,method,params:{_meta:meta()}};
  const started=new Date().toISOString(); const t0=performance.now();
  try {
    const res=await fetch(url,{
      method:'POST',
      headers:{
        'content-type':'application/json',
        'accept':'application/json, text/event-stream',
        'user-agent':'RISU-SIGMETRICS-Longitudinal/1.0',
        'mcp-protocol-version':SPEC,
        'mcp-method':method,
        'mcp-name':''
      },
      body:JSON.stringify(body), signal:ctl.signal, redirect:'follow'
    });
    const text=await res.text();
    const ms=performance.now()-t0;
    const obj=extractResult(text,res.headers.get('content-type'));
    const result=obj?.result ?? null;
    let errorClass='';
    if (res.status !== 200) errorClass=`http_${res.status}`;
    else if (!obj) errorClass='parse_error';
    else if (!result) errorClass='no_result';
    return {
      ok:res.status===200 && !!result,
      status:res.status,
      ms:+ms.toFixed(3),
      started_at:started,
      ended_at:new Date().toISOString(),
      response_bytes:Buffer.byteLength(text),
      content_type:res.headers.get('content-type')||'',
      error_class:errorClass,
      result,
      result_hash:result?sha(result):null,
      ttl_ms:Number.isFinite(Number(result?.ttlMs))?Number(result.ttlMs):null,
      cache_scope:typeof result?.cacheScope==='string'?result.cacheScope:null
    };
  } catch(e) {
    return {
      ok:false,status:0,ms:+(performance.now()-t0).toFixed(3),
      started_at:started,ended_at:new Date().toISOString(),
      response_bytes:0,content_type:'',
      error_class:e.name==='AbortError'?'timeout':'network_error',
      error_detail:String(e.message||e),result:null,result_hash:null,ttl_ms:null,cache_scope:null
    };
  } finally { clearTimeout(timer); }
}
function normalizedTools(result) {
  const tools=Array.isArray(result?.tools)?result.tools:[];
  return [...tools].sort((a,b)=>String(a?.name||'').localeCompare(String(b?.name||'')));
}
async function egressFingerprint() {
  const t0=performance.now();
  try {
    const res=await fetch('https://www.cloudflare.com/cdn-cgi/trace',{headers:{'user-agent':'RISU-SIGMETRICS-Longitudinal/1.0'}});
    const txt=await res.text();
    const kv={}; for(const line of txt.split(/\r?\n/)){const i=line.indexOf('='); if(i>0)kv[line.slice(0,i)]=line.slice(i+1);}
    return {ok:true,colo:kv.colo||null,country:kv.loc||null,ip_hash:kv.ip?sha(kv.ip).slice(0,16):null,ms:+(performance.now()-t0).toFixed(3)};
  } catch(e) { return {ok:false,error:String(e.message||e),ms:+(performance.now()-t0).toFixed(3)}; }
}

mkdirSync('out',{recursive:true});
const panel=loadPanel('experiments/sigmetrics-readiness/frozen_operator_panel_v1.csv');
let selected = MODE==='full' ? panel : panel.filter(x=>x.sentinel);
selected = seededShuffle(selected, WAVE_ID);
const vantage=await egressFingerprint();
console.log('CAMPAIGN',JSON.stringify({wave_id:WAVE_ID,mode:MODE,n:selected.length,vantage,event:EVENT}));

const rows=[];
async function one(ep) {
  const d=await rpc(ep.url,'server/discover');
  const t=await rpc(ep.url,'tools/list');
  const tools=normalizedTools(t.result);
  const names=tools.map(x=>x?.name??'');
  const row={
    campaign_id:'SIGMETRICS-READINESS-LONGITUDINAL-v1',
    wave_id:WAVE_ID,mode:MODE,run_id:RUN_ID,run_attempt:RUN_ATTEMPT,event:EVENT,
    vantage_colo:vantage.colo??null,vantage_country:vantage.country??null,vantage_ip_hash:vantage.ip_hash??null,
    operator:ep.operator,name:ep.name,url:ep.url,sentinel:ep.sentinel,pilot_block:ep.pilot_block,
    discover_ok:d.ok,discover_status:d.status,discover_ms:d.ms,discover_error_class:d.error_class,
    discover_started_at:d.started_at,discover_ended_at:d.ended_at,discover_bytes:d.response_bytes,
    discover_hash:d.result_hash,discover_ttl_ms:d.ttl_ms,discover_cache_scope:d.cache_scope,
    tools_ok:t.ok,tools_status:t.status,tools_ms:t.ms,tools_error_class:t.error_class,
    tools_started_at:t.started_at,tools_ended_at:t.ended_at,tools_bytes:t.response_bytes,
    tools_result_hash:t.result_hash,tools_ttl_ms:t.ttl_ms,tools_cache_scope:t.cache_scope,
    tool_count:tools.length,
    tool_schema_hash:t.ok?sha(tools):null,
    tool_names_hash:t.ok?sha(names):null,
    readiness_ok:d.ok&&t.ok,
    readiness_ms:d.ok&&t.ok?+(d.ms+t.ms).toFixed(3):null,
    observed_at:new Date().toISOString()
  };
  rows.push(row);
  console.log('OBS',JSON.stringify(row));
}
let idx=0;
await Promise.all(Array.from({length:CONCURRENCY},async()=>{
  while(true){const i=idx++; if(i>=selected.length)break; await one(selected[i]);}
}));
rows.sort((a,b)=>a.operator.localeCompare(b.operator));
writeFileSync('out/wave.jsonl',rows.map(x=>JSON.stringify(x)).join('\n')+'\n');
writeFileSync('out/wave_meta.json',JSON.stringify({
  campaign_id:'SIGMETRICS-READINESS-LONGITUDINAL-v1',
  wave_id:WAVE_ID,mode:MODE,n:selected.length,
  created_at:new Date().toISOString(),vantage,
  successes:rows.filter(x=>x.readiness_ok).length,
  tools_successes:rows.filter(x=>x.tools_ok).length,
  discover_successes:rows.filter(x=>x.discover_ok).length
},null,2));
