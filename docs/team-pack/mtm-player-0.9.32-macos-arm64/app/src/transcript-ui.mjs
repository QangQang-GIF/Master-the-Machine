// Embedded by both dashboard pages; all untrusted content uses textContent.
export const transcriptUiScript = String.raw`
function renderTranscriptEntry(item){
  const entry=document.createElement('article');entry.className='entry transcript-entry';
  const header=document.createElement('strong');header.textContent=(item.role==='ASSISTANT'?'Orchestrator':item.role==='TOOL'?'Tool':'System')+' · '+(item.timestamp?new Date(item.timestamp).toLocaleTimeString():'Time unavailable');entry.append(header);
  const body=document.createElement('div');
  if(item.detail){
    const details=document.createElement('details'),summary=document.createElement('summary');
    summary.textContent=item.detail.name+' · '+item.detail.phase;details.append(summary);
    for(const [label,value] of [['Input',item.detail.input],['Output',item.detail.output]]){
      const heading=document.createElement('b'),pre=document.createElement('pre');heading.textContent=label;pre.textContent=value||'(empty)';details.append(heading,pre);
    }
    if(item.detail.exitCode!==null){const code=document.createElement('p');code.textContent='Exit code: '+item.detail.exitCode;details.append(code)}body.append(details);
  }else{
    let code=null;
    for(const line of String(item.text||'').split('\n')){
      if(line.startsWith('~~~')||line.startsWith(String.fromCharCode(96).repeat(3))){if(code)code=null;else{code=document.createElement('pre');body.append(code)}continue}
      if(code){code.textContent+=line+'\n';continue}
      const p=document.createElement(/^#{1,3} /.test(line)?'h4':'p');
      const value=line.replace(/^#{1,3} /,'');
      value.split(/(\*\*[^*]+\*\*)/g).forEach(part=>{const n=document.createElement(part.startsWith('**')?'strong':'span');n.textContent=part.startsWith('**')?part.slice(2,-2):part;p.append(n)});body.append(p);
    }
  }
  entry.append(body);return entry;
}
`;
