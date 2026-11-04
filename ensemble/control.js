const partitionTokenInput=document.getElementById("partition-token");
const partitionIdInput=document.getElementById("partition-id");
const partitionSessionStatus=document.getElementById("partition-session-status");
try{
 partitionTokenInput.value=sessionStorage.getItem("ensemble.partition.token")||"";
 partitionIdInput.value=sessionStorage.getItem("ensemble.partition.id")||"";
 if(partitionTokenInput.value)partitionSessionStatus.textContent="Credentials loaded for this tab.";
}catch(error){partitionSessionStatus.textContent="Session storage is unavailable; credentials will remain in the fields only.";}
document.getElementById("connect-partition").addEventListener("click",()=>{
 try{
  sessionStorage.setItem("ensemble.partition.token",partitionTokenInput.value.trim());
  sessionStorage.setItem("ensemble.partition.id",partitionIdInput.value.trim());
  partitionSessionStatus.textContent="Credentials saved for this tab. Protected requests will use this partition.";
 }catch(error){partitionSessionStatus.textContent="Could not save credentials in session storage; the fields remain active.";}
});
document.getElementById("clear-partition").addEventListener("click",()=>{
 partitionTokenInput.value="";partitionIdInput.value="";
 try{sessionStorage.removeItem("ensemble.partition.token");sessionStorage.removeItem("ensemble.partition.id");}catch(error){}
 partitionSessionStatus.textContent="Credentials cleared. Local default mode will be used.";
});
const nativeFetch=window.fetch.bind(window);
window.fetch=(input,options={})=>{
 const url=typeof input==="string"?input:(input&&input.url)||"";
 if(url.startsWith("/api/v1/")){
  const headers=new Headers(options.headers||(input instanceof Request?input.headers:undefined));
  const token=(partitionTokenInput.value||"").trim();
  const partition=(partitionIdInput.value||"").trim();
  if(token)headers.set("Authorization","Bearer "+token);
  if(partition)headers.set("X-Ensemble-Partition",partition);
  options={...options,headers};
 }
 return nativeFetch(input,options);
};
const form=document.getElementById("scrape-form");
const uri=document.getElementById("uri");
const status=document.getElementById("status");
const panel=document.getElementById("preview");
const title=document.getElementById("title");
const metadata=document.getElementById("metadata");
const body=document.getElementById("body");
const links=document.getElementById("links");
const ingest=document.getElementById("ingest");
let ready=false;
uri.addEventListener("input",()=>{ready=false;ingest.disabled=true;status.textContent="URI changed. Fetch a fresh preview before ingesting.";});
async function request(path,payload){
 const response=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
 const result=await response.json();
 if(!response.ok)throw new Error(typeof result.detail==="string"?result.detail:JSON.stringify(result.detail));
 return result;
}
form.addEventListener("submit",async event=>{
 event.preventDefault();ready=false;ingest.disabled=true;status.textContent="Fetching preview…";
 try{
  const requestedUri=uri.value.trim();
  const result=await request("/api/v1/ingest/urls/preview",{uri:requestedUri});
  if(uri.value.trim()!==requestedUri){status.textContent="URI changed. Fetch a fresh preview.";return;}
  title.textContent=result.title;
  metadata.textContent=result.final_uri+" | "+result.characters+" characters | "+result.media_type;
  body.textContent=result.text;
  links.replaceChildren();
  for(const href of result.links){const link=document.createElement("a");link.href=href;link.textContent=href;link.target="_blank";link.rel="noopener noreferrer";links.append(link,document.createElement("br"));}
  panel.hidden=false;ready=true;ingest.disabled=false;
  status.textContent="Preview ready. No content has been ingested.";
 }catch(error){status.textContent="Preview failed: "+error.message;}
});
ingest.addEventListener("click",async()=>{
 if(!ready)return;
 ingest.disabled=true;status.textContent="Ingesting page…";
 try{
  const result=await request("/api/v1/ingest/urls",{uri:uri.value.trim()});
  status.textContent="Ingested: "+JSON.stringify(result.document);
 }catch(error){status.textContent="Ingestion failed: "+error.message;ingest.disabled=false;}
});

const refreshHealth=document.getElementById("refresh-health");
const healthResult=document.getElementById("health-result");
refreshHealth.addEventListener("click",async()=>{
 refreshHealth.disabled=true;
 try{
  const response=await fetch("/ready");
  const payload=await response.json();
  healthResult.textContent=JSON.stringify(payload,null,2);
 }catch(error){healthResult.textContent="Readiness check failed: "+error.message;}
 finally{refreshHealth.disabled=false;}
});

const gaForm=document.getElementById("ga-form");
const gaPopulation=document.getElementById("ga-population");
const gaGenerations=document.getElementById("ga-generations");
const gaSeed=document.getElementById("ga-seed");
const gaStart=document.getElementById("ga-start");
const gaRefresh=document.getElementById("ga-refresh");
const gaStatus=document.getElementById("ga-status");
const gaRuns=document.getElementById("ga-runs");
const gaDetail=document.getElementById("ga-detail");
const gaResult=document.getElementById("ga-result");
const gaCandidates=document.getElementById("ga-candidates");
const gaApprovedBy=document.getElementById("ga-approved-by");
const gaReason=document.getElementById("ga-reason");
let selectedGaRun=null;

async function gaApi(path,method="GET",payload=null){
 const options={method,headers:{}};
 if(payload!==null){options.headers["Content-Type"]="application/json";options.body=JSON.stringify(payload);}
 const response=await fetch(path,options);
 const result=await response.json();
 if(!response.ok)throw new Error(typeof result.detail==="string"?result.detail:JSON.stringify(result.detail));
 return result;
}
function gaText(value){return document.createTextNode(String(value));}
function showGaRun(run){
 selectedGaRun=run.id;
 gaDetail.hidden=false;
 gaResult.textContent=JSON.stringify({
  id:run.id,status:run.status,config:run.config,error:run.error,
  result:run.result?{
   seed:run.result.seed,total_evaluations:run.result.total_evaluations,
   evaluation_scope:run.result.evaluation_scope,
   fitness_history:run.result.fitness_history,
   best_by_system:run.result.best_by_system
  }:null
 },null,2);
 gaCandidates.replaceChildren();
 if(run.status!=="completed"||!run.result)return;
 for(const [system,candidates] of Object.entries(run.result.best_by_system||{})){
  const section=document.createElement("section");
  const heading=document.createElement("h4");
  heading.textContent=system;
  section.append(heading);
  for(const candidate of candidates){
   const row=document.createElement("p");
   row.append(gaText("Rank "+candidate.rank+" | fitness "+Number(candidate.fitness).toFixed(5)+" | "));
   const params=document.createElement("code");
   params.textContent=JSON.stringify(candidate.parameters);
   row.append(params,gaText(" "));
   const approve=document.createElement("button");
   approve.type="button";
   approve.textContent="Approve for export";
   approve.addEventListener("click",async()=>{
    approve.disabled=true;
    try{
     const result=await gaApi("/api/v1/ga/runs/"+encodeURIComponent(run.id)+"/promote","POST",{
      approved:true,system,rank:candidate.rank,
      approved_by:gaApprovedBy.value.trim(),reason:gaReason.value.trim()
     });
     gaStatus.textContent=result.message+" "+JSON.stringify(result.promotion);
    }catch(error){gaStatus.textContent="Approval failed: "+error.message;approve.disabled=false;}
   });
   row.append(approve);
   section.append(row);
  }
  gaCandidates.append(section);
 }
}
async function refreshGaRuns(){
 gaRefresh.disabled=true;
 try{
  const response=await gaApi("/api/v1/ga/runs?limit=20");
  gaRuns.replaceChildren();
  for(const run of response.runs){
   const button=document.createElement("button");
   button.type="button";
   button.textContent=run.created_at+" | "+run.status+" | "+run.id;
   button.addEventListener("click",async()=>{
    try{const detail=await gaApi("/api/v1/ga/runs/"+encodeURIComponent(run.id));showGaRun(detail.run);}
    catch(error){gaStatus.textContent="Could not load run: "+error.message;}
   });
   gaRuns.append(button,document.createElement("br"));
  }
  if(selectedGaRun){
   const selected=await gaApi("/api/v1/ga/runs/"+encodeURIComponent(selectedGaRun));
   showGaRun(selected.run);
  }
  gaStatus.textContent=response.runs.length+" GA run(s) found.";
 }catch(error){gaStatus.textContent="Could not refresh GA runs: "+error.message;}
 finally{gaRefresh.disabled=false;}
}
gaForm.addEventListener("submit",async event=>{
 event.preventDefault();
 gaStart.disabled=true;
 gaStatus.textContent="Starting bounded GA run…";
 try{
  const payload={
   population_size:Number(gaPopulation.value),
   generations:Number(gaGenerations.value)
  };
  if(gaSeed.value.trim()!=="")payload.seed=Number(gaSeed.value);
  const result=await gaApi("/api/v1/ga/runs","POST",payload);
  selectedGaRun=result.run.id;
  gaStatus.textContent="Run queued: "+selectedGaRun;
  await refreshGaRuns();
 }catch(error){gaStatus.textContent="Could not start GA run: "+error.message;}
 finally{gaStart.disabled=false;}
});
gaRefresh.addEventListener("click",refreshGaRuns);
refreshGaRuns();

const thompsonForm=document.getElementById("thompson-select-form");
const thompsonTask=document.getElementById("thompson-task");
const thompsonMaxCost=document.getElementById("thompson-max-cost");
const thompsonSelection=document.getElementById("thompson-selection");
const thompsonRefresh=document.getElementById("thompson-refresh");
const thompsonState=document.getElementById("thompson-state");
async function refreshThompson(){
 thompsonRefresh.disabled=true;
 try{
  const response=await fetch("/api/v1/thompson/state");
  const payload=await response.json();
  if(!response.ok)throw new Error(JSON.stringify(payload.detail||payload));
  const models=payload.state.models||{};
  thompsonState.replaceChildren();
  const table=document.createElement("table");
  const head=document.createElement("tr");
  for(const label of ["Model","Successes","Failures","Calls","Avg cost","Total cost","Tokens","Action"]){
   const cell=document.createElement("th");cell.textContent=label;head.append(cell);
  }
  table.append(head);
  for(const [name,stats] of Object.entries(models)){
   const row=document.createElement("tr");
   const values=[name,stats.successes,stats.failures,stats.calls,
    stats.calls?(stats.total_cost/stats.calls).toFixed(6):"0",
    Number(stats.total_cost).toFixed(6),stats.total_tokens];
   for(const value of values){const cell=document.createElement("td");cell.textContent=String(value);row.append(cell);}
   const action=document.createElement("td");
   const reset=document.createElement("button");reset.type="button";reset.textContent="Reset";
   reset.addEventListener("click",async()=>{
    if(!window.confirm("Reset Thompson history for "+name+"?"))return;
    reset.disabled=true;
    try{
     const result=await fetch("/api/v1/thompson/reset",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({model:name})});
     const data=await result.json();if(!result.ok)throw new Error(JSON.stringify(data.detail||data));
     await refreshThompson();
    }catch(error){thompsonSelection.textContent="Reset failed: "+error.message;reset.disabled=false;}
   });
   action.append(reset);row.append(action);table.append(row);
  }
  thompsonState.append(table);
  if(!Object.keys(models).length)thompsonState.textContent="No model outcomes recorded yet. Selection falls back to haiku.";
 }catch(error){thompsonState.textContent="Could not load Thompson state: "+error.message;}
 finally{thompsonRefresh.disabled=false;}
}
thompsonForm.addEventListener("submit",async event=>{
 event.preventDefault();
 const payload={task_type:thompsonTask.value.trim()||"unknown"};
 if(thompsonMaxCost.value.trim()!=="")payload.max_cost=Number(thompsonMaxCost.value);
 thompsonSelection.textContent="Selecting model…";
 try{
  const result=await fetch("/api/v1/thompson/select",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const data=await result.json();if(!result.ok)throw new Error(JSON.stringify(data.detail||data));
  thompsonSelection.textContent="Selected "+data.model+" (request "+data.request_id+")";
 }catch(error){thompsonSelection.textContent="Selection failed: "+error.message;}
});
thompsonRefresh.addEventListener("click",refreshThompson);
refreshThompson();

const learningRefresh=document.getElementById("learning-refresh");
const learningReset=document.getElementById("learning-reset");
const learningStatus=document.getElementById("learning-status");
const learningReport=document.getElementById("learning-report");
const learningOutcomes=document.getElementById("learning-outcomes");
const learningArtifacts=document.getElementById("learning-artifacts");
async function learningGet(path,options={}){
 const response=await fetch(path,options);
 const result=await response.json();
 if(!response.ok)throw new Error(JSON.stringify(result.detail||result));
 return result;
}
async function refreshLearning(){
 learningRefresh.disabled=true;
 try{
  const [report,outcomes,artifacts]=await Promise.all([
   learningGet("/api/v1/learning/report"),
   learningGet("/api/v1/learning/outcomes?days=30"),
   learningGet("/api/v1/learning/artifacts?limit=50")
  ]);
  learningReport.textContent=JSON.stringify(report,null,2);
  learningOutcomes.replaceChildren();
  const table=document.createElement("table");
  const header=document.createElement("tr");
  for(const label of ["Task ID","Task type","Model","Rating","Tokens","Cost","Status"]){
   const cell=document.createElement("th");cell.textContent=label;header.append(cell);
  }
  table.append(header);
  for(const item of outcomes.outcomes){
   const row=document.createElement("tr");
   for(const value of [item.task_id,item.task_type,item.model,item.rating,item.tokens,item.cost,item.status]){
    const cell=document.createElement("td");cell.textContent=String(value);row.append(cell);
   }
   table.append(row);
  }
  learningOutcomes.append(table);
  if(!outcomes.outcomes.length)learningOutcomes.textContent="No outcomes in the selected period.";
  learningArtifacts.replaceChildren();
  for(const artifact of artifacts.artifacts){
   const item=document.createElement("p");
   item.textContent=artifact.artifact_type+" | "+artifact.artifact_key+" | "+artifact.created_at;
   learningArtifacts.append(item);
  }
  if(!artifacts.artifacts.length)learningArtifacts.textContent="No portable artifacts recorded.";
  learningStatus.textContent=outcomes.outcomes.length+" recent outcome(s), "+artifacts.artifacts.length+" artifact(s).";
 }catch(error){learningStatus.textContent="Could not load Learning state: "+error.message;}
 finally{learningRefresh.disabled=false;}
}
learningRefresh.addEventListener("click",refreshLearning);
learningReset.addEventListener("click",async()=>{
 if(!window.confirm("Reset all Learning outcomes? Portable artifacts and Thompson model statistics will be preserved."))return;
 learningReset.disabled=true;
 try{
  await learningGet("/api/v1/learning/reset",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({confirm:true})});
  learningStatus.textContent="Learning outcomes reset. Thompson state and artifacts were preserved.";
  await refreshLearning();
 }catch(error){learningStatus.textContent="Reset failed: "+error.message;}
 finally{learningReset.disabled=false;}
});
refreshLearning();

const runtimeConfigValues=document.getElementById("runtime-config-values");
if(runtimeConfigValues){
 fetch("/api/v1/system/config")
  .then(async response=>{
   const value=await response.json();
   if(!response.ok)throw new Error(value.detail||"configuration request failed");
   runtimeConfigValues.textContent=JSON.stringify(value,null,2);
  })
  .catch(error=>{runtimeConfigValues.textContent="Could not load runtime configuration: "+error.message;});
}


// Capability, retrieval, Index, Vector, and Memory inspection workbenches.
async function controlJson(path, method="GET", payload=null){
 const options={method,headers:{}};
 if(payload!==null){options.headers["Content-Type"]="application/json";options.body=JSON.stringify(payload);}
 const response=await fetch(path,options);
 let data={};
 try{data=await response.json();}catch(_error){data={detail:"Non-JSON response"};}
 if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:JSON.stringify(data.detail||data));
 return data;
}
const capabilities=[
 ["Embeddings","/api/v1/embeddings/health",null],
 ["Vector","/api/v1/vector/health",null],
 ["Lexical","/api/v1/search/lexical/health","/api/v1/search/lexical/ready"],
 ["Hybrid","/api/v1/search/hybrid/health","/api/v1/search/hybrid/ready"],
 ["Reranking","/api/v1/reranking/health","/api/v1/reranking/ready"],
 ["Chunking","/api/v1/chunking/health","/api/v1/chunking/ready"],
 ["Tokenization","/api/v1/tokenization/health","/api/v1/tokenization/ready"],
 ["Index","/api/v1/index/health","/api/v1/index/ready"],
 ["Entities","/api/v1/entities/health","/api/v1/entities/ready"],
 ["Intent","/api/v1/intent/health","/api/v1/intent/ready"],
 ["Ranking","/api/v1/ranking/health","/api/v1/ranking/ready"],
 ["Knowledge","/api/v1/knowledge/health","/api/v1/knowledge/ready"],
 ["Memory","/api/v1/memory/health","/api/v1/memory/memories/{name}y"],
 ["Graph","/api/v1/graph/health",null],
 ["Thompson","/api/v1/thompson/health","/api/v1/thompson/ready"],
 ["Learning","/api/v1/learning/health","/api/v1/learning/ready"],
 ["GA Tuning","/api/v1/ga/health","/api/v1/ga/ready"]
];
const capabilityRows=document.getElementById("capability-rows");
const capabilitySummary=document.getElementById("capability-summary");
const refreshCapabilities=document.getElementById("refresh-capabilities");
async function refreshCapabilityTable(){
 refreshCapabilities.disabled=true;
 capabilitySummary.textContent="Checking "+capabilities.length+" capabilities…";
 capabilityRows.replaceChildren();
 let readyCount=0,failedCount=0;
 const checks=await Promise.all(capabilities.map(async([name,healthPath,readyPath])=>{
  const check=async path=>{
   if(!path)return {status:"n/a",body:null};
   try{
    const response=await fetch(path);
    let body={};try{body=await response.json();}catch(_error){}
    return {status:response.ok?"ok":"unavailable",body};
   }catch(error){return {status:"error",body:{detail:error.message}};}
  };
  const [health,ready]=await Promise.all([check(healthPath),check(readyPath)]);
  return {name,health,ready};
 }));
 for(const item of checks){
  const row=document.createElement("tr");
  const name=document.createElement("td");name.textContent=item.name;
  const health=document.createElement("td");health.textContent=item.health.status;
  health.className=item.health.status==="ok"?"status-ok":"status-error";
  const ready=document.createElement("td");ready.textContent=item.ready.status;
  ready.className=item.ready.status==="ok"?"status-ok":item.ready.status==="unavailable"?"status-error":"muted";
  const detail=document.createElement("td");
  detail.textContent=JSON.stringify(item.health.body||item.ready.body||{});
  row.append(name,health,ready,detail);capabilityRows.append(row);
  if(item.health.status==="ok"&&(item.ready.status==="ok"||item.ready.status==="n/a"))readyCount++;
  if(item.health.status!=="ok")failedCount++;
 }
 capabilitySummary.textContent=readyCount+" healthy, "+failedCount+" unavailable or disabled.";
 refreshCapabilities.disabled=false;
}
refreshCapabilities.addEventListener("click",refreshCapabilityTable);
refreshCapabilityTable();

const retrievalForm=document.getElementById("retrieval-form");
const retrievalQuery=document.getElementById("retrieval-query");
const retrievalLimit=document.getElementById("retrieval-limit");
const retrievalResults=document.getElementById("retrieval-results");
function renderRetrievalPipeline(result){
 retrievalResults.replaceChildren();
 const fusion=result.fusion||{};
 const summary=document.createElement("p");
 summary.textContent="Pipeline: candidate retrieval → "+(fusion.algorithm||"fusion")+" (k="+String(fusion.k??"n/a")+") → "+(result.reranked?"reranking applied":"optional reranking")+
  ". Weight strategy: "+(fusion.weight_strategy||"static")+
  ". Retrieval status: "+(result.status||"unknown")+
  (result.degraded?.length?"; degraded: "+result.degraded.join(", "):"");
 retrievalResults.append(summary);
 const weights=document.createElement("p");
 weights.textContent="Channel weights: "+JSON.stringify(fusion.weights||{});
 retrievalResults.append(weights);
 const channelTable=document.createElement("table");
 const channelHead=document.createElement("tr");
 for(const label of ["Channel","Candidates","Status"]){
  const cell=document.createElement("th");cell.textContent=label;channelHead.append(cell);
 }
 channelTable.append(channelHead);
 for(const [name,details] of Object.entries(fusion.channels||{})){
  const row=document.createElement("tr");
  for(const value of [name,details.candidate_count??0,result.channels?.[name]?.status||"retrieved"]){
   const cell=document.createElement("td");cell.textContent=String(value);row.append(cell);
  }
  channelTable.append(row);
 }
 retrievalResults.append(channelTable);
 const fusionTitle=document.createElement("h3");fusionTitle.textContent="RRF contributions";retrievalResults.append(fusionTitle);
 const fusionTable=document.createElement("table");
 const fusionHead=document.createElement("tr");
 for(const label of ["Candidate","Ranks","Contributions","Fused score"]){
  const cell=document.createElement("th");cell.textContent=label;fusionHead.append(cell);
 }
 fusionTable.append(fusionHead);
 for(const item of fusion.results||[]){
  const row=document.createElement("tr");
  for(const value of [
   item.chunk_id,
   JSON.stringify(item.ranks||{lexical:item.lexical_rank,vector:item.vector_rank,graph:item.graph_rank}),
   JSON.stringify(item.contributions||{}),
   item.score
  ]){
   const cell=document.createElement("td");cell.textContent=String(value??"");row.append(cell);
  }
  fusionTable.append(row);
 }
 retrievalResults.append(fusionTable);
 const graph=result.graph_expansion||{};
 const graphSummary=document.createElement("p");
 graphSummary.textContent="Graph expansion: "+String(graph.candidate_sources||0)+" sources, "+String(graph.candidate_chunks||0)+" candidate chunks, "+String(graph.duration_ms??0)+" ms.";
 retrievalResults.append(graphSummary);
 const resultTitle=document.createElement("h3");resultTitle.textContent="Final results";retrievalResults.append(resultTitle);
 const resultTable=document.createElement("table");
 const resultHead=document.createElement("tr");
 for(const label of ["Source","Chunk","RRF score","Graph score","Rerank score"]){
  const cell=document.createElement("th");cell.textContent=label;resultHead.append(cell);
 }
 resultTable.append(resultHead);
 for(const item of result.results||[]){
  const row=document.createElement("tr");
  for(const value of [item.source_id,item.chunk_id,item.fusion_score,item.graph_score,item.rerank_score]){
   const cell=document.createElement("td");cell.textContent=String(value??"");row.append(cell);
  }
  resultTable.append(row);
 }
 retrievalResults.append(resultTable);
 const raw=document.createElement("details");
 const disclosure=document.createElement("summary");disclosure.textContent="Full response JSON";raw.append(disclosure);
 const pre=document.createElement("pre");pre.textContent=JSON.stringify(result,null,2);raw.append(pre);
 retrievalResults.append(raw);
}
retrievalForm.addEventListener("submit",async event=>{
 event.preventDefault();retrievalResults.textContent="Searching…";
 try{
  const result=await controlJson("/api/v1/search/hybrid","POST",{
   query:retrievalQuery.value.trim(),limit:Number(retrievalLimit.value),
   rerank:document.getElementById("retrieval-rerank").checked
  });
  renderRetrievalPipeline(result);
 }catch(error){retrievalResults.textContent="Search failed: "+error.message;}
});

const indexResults=document.getElementById("index-results");
const vectorResults=document.getElementById("vector-results");
document.getElementById("refresh-index").addEventListener("click",async()=>{
 indexResults.textContent="Loading source manifest…";
 try{indexResults.textContent=JSON.stringify(await controlJson("/api/v1/index/manifest?limit=100"),null,2);}
 catch(error){indexResults.textContent="Index inspection failed: "+error.message;}
});
document.getElementById("refresh-vectors").addEventListener("click",async()=>{
 vectorResults.textContent="Loading vector collections…";
 try{vectorResults.textContent=JSON.stringify(await controlJson("/api/v1/vector/collections"),null,2);}
 catch(error){vectorResults.textContent="Vector inspection failed: "+error.message;}
});
document.getElementById("source-lookup-form").addEventListener("submit",async event=>{
 event.preventDefault();
 const id=document.getElementById("source-id").value.trim();
 const output=document.getElementById("source-result");
 output.textContent="Loading source…";
 try{output.textContent=JSON.stringify(await controlJson("/api/v1/index/sources/"+encodeURIComponent(id)),null,2);}
 catch(error){output.textContent="Source lookup failed: "+error.message;}
});

const memoryNames=document.getElementById("memory-names");
const memoryKnowledge=document.getElementById("memory-knowledge");
const memoryResults=document.getElementById("memory-results");
document.getElementById("refresh-memories").addEventListener("click",async()=>{
 memoryNames.textContent="Loading memory names…";
 try{memoryNames.textContent=JSON.stringify(await controlJson("/api/v1/memory/memories"),null,2);}
 catch(error){memoryNames.textContent="Memory inspection failed: "+error.message;}
});
document.getElementById("refresh-memory-knowledge").addEventListener("click",async()=>{
 memoryKnowledge.textContent="Loading Knowledge status…";
 try{memoryKnowledge.textContent=JSON.stringify(await controlJson("/api/v1/memory/sources/status"),null,2);}
 catch(error){memoryKnowledge.textContent="Knowledge status failed: "+error.message;}
});
document.getElementById("memory-search-form").addEventListener("submit",async event=>{
 event.preventDefault();memoryResults.textContent="Searching Memory…";
 try{
  memoryResults.textContent=JSON.stringify(await controlJson("/api/v1/memory/search/hybrid","POST",{
   query:document.getElementById("memory-query").value.trim(),top_k:10
  }),null,2);
 }catch(error){memoryResults.textContent="Memory search failed: "+error.message;}
});

document.getElementById("source-tombstone-form").addEventListener("submit",async event=>{
 event.preventDefault();
 const sourceId=document.getElementById("tombstone-source-id").value.trim();
 const sequence=Number(document.getElementById("tombstone-sequence").value);
 const output=document.getElementById("tombstone-result");
 if(!window.confirm("Permanently tombstone source "+sourceId+" at revision "+sequence+"?"))return;
 output.textContent="Recording tombstone…";
 try{
  const result=await controlJson("/api/v1/index/tombstones","POST",{
   source_id:sourceId,revision_sequence:sequence
  });
  output.textContent=JSON.stringify(result,null,2);
  document.getElementById("refresh-index").click();
 }catch(error){output.textContent="Tombstone failed: "+error.message;}
});

document.getElementById("session-ingest-button").addEventListener("click",async()=>{
 const button=document.getElementById("session-ingest-button");
 const status=document.getElementById("session-ingest-status");
 const output=document.getElementById("session-ingest-result");
 button.disabled=true;status.textContent="Validating and ingesting session…";
 try{
  const payload=JSON.parse(document.getElementById("session-payload").value);
  const result=await controlJson("/api/v1/ingest/sessions","POST",payload);
  status.textContent=result.inserted+" new, "+result.already_present+" already present.";
  output.textContent=JSON.stringify(result,null,2);
  document.getElementById("refresh-index").click();
 }catch(error){status.textContent="Session ingestion failed: "+error.message;}
 finally{button.disabled=false;}
});

/* Read-only capability workbenches and interactive diagnostic pipeline. */
async function loadProfileOptions(path,selectId,resultsId){
 const select=document.getElementById(selectId),output=document.getElementById(resultsId);
 try{
  const data=await controlJson(path);
  const profiles=Array.isArray(data.profiles)?data.profiles:[];
  select.replaceChildren();
  for(const profile of profiles){
   const name=typeof profile==="string"?profile:profile.name;
   if(!name)continue;
   const option=document.createElement("option");option.value=name;option.textContent=name;select.append(option);
  }
  output.textContent=JSON.stringify(data,null,2);
  return profiles;
 }catch(error){output.textContent="Could not load profiles: "+error.message;return [];}
}
document.getElementById("chunking-refresh").addEventListener("click",()=>loadProfileOptions("/api/v1/chunking/profiles","chunking-profile","chunking-results"));
document.getElementById("tokenization-refresh").addEventListener("click",()=>loadProfileOptions("/api/v1/tokenization/profiles","tokenization-profile","tokenization-results"));
loadProfileOptions("/api/v1/chunking/profiles","chunking-profile","chunking-results");
loadProfileOptions("/api/v1/tokenization/profiles","tokenization-profile","tokenization-results");
document.getElementById("chunking-preview").addEventListener("click",async()=>{
 const button=document.getElementById("chunking-preview"),output=document.getElementById("chunking-results");
 button.disabled=true;
 try{
  const result=await controlJson("/api/v1/chunking/chunk","POST",{
   profile:document.getElementById("chunking-profile").value,
   source_id:"control-center-preview",
   text:document.getElementById("chunking-text").value,
   media_type:"text/plain"
  });
  output.textContent=JSON.stringify(result,null,2);
 }catch(error){output.textContent="Chunk preview failed: "+error.message;}
 finally{button.disabled=false;}
});
document.getElementById("tokenization-preview").addEventListener("click",async()=>{
 const button=document.getElementById("tokenization-preview"),output=document.getElementById("tokenization-results");
 button.disabled=true;
 try{
  const result=await controlJson("/api/v1/tokenization/tokenize","POST",{
   profile:document.getElementById("tokenization-profile").value,
   text:document.getElementById("tokenization-text").value
  });
  output.textContent=JSON.stringify(result,null,2);
 }catch(error){output.textContent="Tokenization failed: "+error.message;}
 finally{button.disabled=false;}
});
document.getElementById("graph-query-form").addEventListener("submit",async event=>{
 event.preventDefault();const output=document.getElementById("graph-results");
 try{
  let properties=JSON.parse(document.getElementById("graph-node-properties").value||"{}");
  if(!properties||Array.isArray(properties)||typeof properties!=="object")throw new Error("Properties must be a JSON object.");
  const result=await controlJson("/api/v1/graph/query","POST",{
   type:document.getElementById("graph-node-type").value.trim()||null,properties
  });
  output.textContent=JSON.stringify(result,null,2);
 }catch(error){output.textContent="Graph query failed: "+error.message;}
});
document.getElementById("graph-node-form").addEventListener("submit",async event=>{
 event.preventDefault();const output=document.getElementById("graph-results");
 try{output.textContent=JSON.stringify(await controlJson("/api/v1/graph/nodes/"+encodeURIComponent(document.getElementById("graph-node-id").value.trim())),null,2);}
 catch(error){output.textContent="Node lookup failed: "+error.message;}
});
document.getElementById("graph-traverse-form").addEventListener("submit",async event=>{
 event.preventDefault();const output=document.getElementById("graph-results");
 try{
  const edgeType=document.getElementById("graph-edge-type").value.trim();
  const result=await controlJson("/api/v1/graph/traverse","POST",{
   start:document.getElementById("graph-start-id").value.trim(),
   direction:document.getElementById("graph-direction").value,
   max_depth:Number(document.getElementById("graph-depth").value),
   edge_type:edgeType||null
  });
  output.textContent=JSON.stringify(result,null,2);
 }catch(error){output.textContent="Graph traversal failed: "+error.message;}
});
document.getElementById("knowledge-lookup-form").addEventListener("submit",async event=>{
 event.preventDefault();const output=document.getElementById("knowledge-results");
 try{
  const id=document.getElementById("knowledge-source-id").value.trim();
  output.textContent=JSON.stringify(await controlJson("/api/v1/knowledge/records/"+encodeURIComponent(id)),null,2);
 }catch(error){output.textContent="Knowledge lookup failed: "+error.message;}
});
document.getElementById("pipeline-run").addEventListener("click",async()=>{
 const button=document.getElementById("pipeline-run"),output=document.getElementById("pipeline-results");
 const textValue=document.getElementById("pipeline-text").value;
 if(!textValue.trim()){output.textContent="Enter text before running the diagnostic pipeline.";return;}
 button.disabled=true;output.textContent="Running independent read-only capability probes…";
 const stages=[
  ["Tokenization",()=>controlJson("/api/v1/tokenization/tokenize","POST",{profile:document.getElementById("tokenization-profile").value||"lexical-v1",text:textValue})],
  ["Entities",()=>controlJson("/api/v1/entities/extract","POST",{text:textValue,source:"control-center.diagnostic"})],
  ["Intent",()=>controlJson("/api/v1/intent/classify","POST",{text:textValue})],
  ["Chunking",()=>controlJson("/api/v1/chunking/chunk","POST",{profile:document.getElementById("chunking-profile").value||"window-v1",source_id:"control-center-diagnostic",text:textValue,media_type:"text/plain"})]
 ];
 const trace={kind:"interactive-diagnostic-preview",persisted:false,warning:"These are independent current capability probes, not a correlated execution trace of ingestion.",stages:[]};
 try{
  for(const [name,run] of stages){
   const started=performance.now();
   try{const result=await run();trace.stages.push({name,status:"completed",duration_ms:Math.round(performance.now()-started),result});}
   catch(error){trace.stages.push({name,status:"failed",duration_ms:Math.round(performance.now()-started),error:error.message});}
  }
  output.textContent=JSON.stringify(trace,null,2);
 }finally{button.disabled=false;}
});


let latestEntityCandidates=[];
document.getElementById("entity-extract").addEventListener("click",async()=>{
 const button=document.getElementById("entity-extract"),resolveButton=document.getElementById("entity-resolve"),output=document.getElementById("entity-results");
 button.disabled=true;resolveButton.disabled=true;
 try{
  const result=await controlJson("/api/v1/entities/extract","POST",{
   text:document.getElementById("entity-text").value,
   source:"control-center.entity-workbench"
  });
  latestEntityCandidates=result.entities||[];
  output.textContent=JSON.stringify(result,null,2);
  resolveButton.disabled=latestEntityCandidates.length===0;
 }catch(error){latestEntityCandidates=[];output.textContent="Entity extraction failed: "+error.message;}
 finally{button.disabled=false;}
});
document.getElementById("entity-resolve").addEventListener("click",async()=>{
 const button=document.getElementById("entity-resolve"),output=document.getElementById("entity-results");
 if(!latestEntityCandidates.length){output.textContent="Extract entity candidates first.";return;}
 button.disabled=true;
 try{
  const result=await controlJson("/api/v1/entities/resolve","POST",{entities:latestEntityCandidates});
  output.textContent=JSON.stringify({candidates:latestEntityCandidates,resolution:result},null,2);
 }catch(error){output.textContent="Entity resolution failed: "+error.message;}
 finally{button.disabled=false;}
});
document.getElementById("entity-link-form").addEventListener("submit",async event=>{
 event.preventDefault();const output=document.getElementById("entity-link-result");
 try{
  const result=await controlJson("/api/v1/entities/link","POST",{
   source_id:document.getElementById("entity-source-id").value.trim(),
   target_id:document.getElementById("entity-target-id").value.trim(),
   relationship:document.getElementById("entity-relationship").value.trim()
  });
  output.textContent=JSON.stringify(result,null,2);
 }catch(error){output.textContent="Entity linking failed: "+error.message;}
});

document.getElementById("context-form").addEventListener("submit",async event=>{
 event.preventDefault();
 const button=event.submitter,output=document.getElementById("context-results");
 const budget=Number(document.getElementById("context-budget").value);
 button.disabled=true;
 try{
  const result=await controlJson("/api/v1/context/assemble","POST",{
   query:document.getElementById("context-query").value,
   max_tokens:budget,
   limit:10,
   include_memory:document.getElementById("context-memory").checked,
   include_search:document.getElementById("context-search").checked
  });
  output.textContent=JSON.stringify(result,null,2);
 }catch(error){output.textContent="Context assembly failed: "+error.message;}
 finally{button.disabled=false;}
});
