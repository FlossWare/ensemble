const form=document.getElementById("scrape-form");
const uri=document.getElementById("uri");
const status=document.getElementById("status");
const panel=document.getElementById("preview");
const title=document.getElementById("title");
const metadata=document.getElementById("metadata");
const body=document.getElementById("body");
const ingest=document.getElementById("ingest");
let ready=false;
async function request(path,payload){
 const response=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
 const result=await response.json();
 if(!response.ok)throw new Error(typeof result.detail==="string"?result.detail:JSON.stringify(result.detail));
 return result;
}
form.addEventListener("submit",async event=>{
 event.preventDefault();ready=false;ingest.disabled=true;status.textContent="Fetching preview…";
 try{
  const result=await request("/api/v1/scrape/preview",{uri:uri.value.trim()});
  title.textContent=result.title;
  metadata.textContent=result.final_uri+" | "+result.characters+" characters | "+result.media_type;
  body.textContent=result.text;
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
