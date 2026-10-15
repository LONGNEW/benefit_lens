export const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export function conditionInput(check,index){
  const e=escapeHtml,id=`condition-${index}`,value=check.value;
  const selected=v=>value===v?' selected':'';
  const attr=`id="${id}" data-check="${index}"`;
  if(check.answer_type==='boolean')return `<select ${attr}><option value="">미입력</option value="true"${selected(true)}>예</option><option value="false"${selected(false)}>아니오</option></select>`;
  if(check.answer_type==='select')return `<select ${attr}><option value="">선택해 주세요</option>${(check.options??[]).map(o=>{const v=typeof o==='object'?o.value:o;return `<option value="${e(v)}"${selected(v)}>${e(typeof o==='object'?o.label:o)}</option>`;}).join('')}</select>`;
  if(check.answer_type==='multi_select')return `<fieldset class="multi-check" id="${id}"><legend>해당하는 항목을 모두 선택해 주세요</legend>${(check.options??[]).map(o=>{const v=typeof o==='object'?o.value:o;return `<label><input type="checkbox" data-check="${index}" value="${e(v)}"${(value??[]).includes(v)?' checked':''}>${e(typeof o==='object'?o.label:o)}</label>`;}).join('')}</fieldset>`;
  if(['number','date'].includes(check.answer_type))return `<input ${attr} type="${check.answer_type}" value="${e(value)}"${check.min!=null?` min="${e(check.min)}"`:''}${check.max!=null?` max="${e(check.max)}"`:''}${check.answer_type==='number'?` step="${e(check.step??'any')}"`:''} placeholder="${check.answer_type==='number'?'숫자를 입력해 주세요':''}">${check.unit?`<span>${e(check.unit)}</span>`:''}`;
  if(check.answer_type==='source_review'){
    const answer=typeof value==='object'&&value!==null?value:{detail:typeof value==='string'?value:''};
    return `<select ${attr} data-part="choice"><option value="">아직 답하지 않음</option>${[['yes','예, 해당합니다'],['no','아니오'],['other','다른 상황입니다'],['unsure','잘 모르겠습니다']].map(([v,label])=>`<option value="${v}"${answer.choice===v?' selected':''}>${label}</option>`).join('')}</select><textarea id="${id}-detail" data-check="${index}" data-part="detail" aria-label="조건에 대한 본인 상황" placeholder="해당하는 범주나 본인 상황을 자유롭게 적어 주세요">${e(answer.detail??'')}</textarea><small>답변을 기록합니다. 아직 조건식으로 해석되지 않아 자동 충족 판정에는 쓰지 않습니다.</small>`;
  }
  return `<textarea ${attr} placeholder="본인 상황을 입력해 주세요">${e(value)}</textarea>`;
}

export function readConditionValue(check,node,nodes=[]){
  if(check.answer_type==='source_review')return Object.fromEntries(nodes.filter(n=>n.dataset.check===node.dataset.check).map(n=>[n.dataset.part,n.value]));
  if(check.answer_type==='multi_select')return nodes.filter(n=>n.dataset.check===node.dataset.check&&n.checked).map(n=>n.value);
  if(node.value==='')return undefined;
  if(check.answer_type==='boolean')return node.value==='true';
  if(check.answer_type==='number')return Number(node.value);
  return node.value;
}
