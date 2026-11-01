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
  const result=await request("/api/v1/scrape/preview",{uri:requestedUri});
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
  const result=await request("/api/v1/scrape/ingest",{uri:uri.value.trim()});
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
