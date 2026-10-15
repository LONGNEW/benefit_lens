export function evaluateEligibility(root, profile) {
  const trace=[];
  const scales=new Set();
  function visit(node){if(!node||typeof node!=='object')return;if(node.field==='education.gpa')scales.add(node.scale);for(const key of ['all','any'])for(const child of node[key]??[])visit(child);if(node.not)visit(node.not);}
  visit(root);
  function evaluate(node){
    if(!node||typeof node!=='object')return null;
    if(node.all){if(!node.all.length)return null;let unknown=false;for(const child of node.all){const value=evaluate(child);if(value===false)return false;unknown ||= value===null;}return unknown?null:true;}
    if(node.any){if(!node.any.length)return null;const values=node.any.map(evaluate);return values.includes(true)?true:values.includes(null)?null:false;}
    if(node.not){const value=evaluate(node.not);return value===null?null:!value;}
    let current=profile[node.field];
    let result=null;
    if(current!==undefined&&current!==null){
      if(node.field==='education.gpa'){
        if(!Number.isFinite(current.value)||!Number.isFinite(current.scale)||current.value<0||current.value>current.scale)result=null;
        else if(!scales.has(current.scale))result=null;
        else if(current.scale!==node.scale)result=false;
        else if(current.degree_scope!==node.degree_scope||current.period_scope!==node.period_scope)result=null;
        else result=current.value>=node.value;
      }else if(node.at&&node.at!=='application')result=null;
      else if(node.op==='eq')result=current===node.value;
      else if(node.op==='in')result=(Array.isArray(current)?current:[current]).some(value=>node.value.includes(value));
      else if(node.op==='not_in')result=!(Array.isArray(current)?current:[current]).some(value=>node.value.includes(value));
      else if(node.op==='gte')result=current>=node.value;
      else if(node.op==='lte')result=current<=node.value;
      else if(node.op==='between')result=current>=node.value[0]&&current<=node.value[1];
    }
    trace.push({field:node.field,result,expected:node.value,scale:node.scale,at:node.at});
    return result;
  }
  const result=evaluate(root);
  return {result,trace};
}

export function applicationState(application, asOf){
  if(application.archived_round)return 'closed';
  if(application.closes_on&&application.closes_on<asOf)return 'closed';
  if(application.opens_on&&application.opens_on>asOf)return 'upcoming';
  if(application.opens_on&&application.closes_on)return 'open';
  return application.rolling?'rolling':'unknown';
}

export function compareValue(value, rule){
  if(!rule||value===undefined||value===null||value===''||(Array.isArray(value)&&!value.length))return null;
  const expected=rule.value,values=Array.isArray(value)?value:[value];
  if(rule.op==='eq')return value===expected;
  if(rule.op==='in')return Array.isArray(expected)?values.some(v=>expected.includes(v)):null;
  if(rule.op==='not_in')return Array.isArray(expected)?values.every(v=>!expected.includes(v)):null;
  if(rule.op==='any_not_in')return Array.isArray(expected)?values.some(v=>!expected.includes(v)):null;
  if(rule.op==='contains_all')return Array.isArray(expected)?expected.every(v=>values.includes(v)):null;
  if(['gte','lte','between'].includes(rule.op)){
    if(typeof value==='number'&&!Number.isFinite(value))return null;
    if(rule.op==='between')return Array.isArray(expected)&&expected.length===2&&expected.every(v=>typeof v===typeof value)?value>=expected[0]&&value<=expected[1]:null;
    if(typeof value!==typeof expected)return null;
    return rule.op==='gte'?value>=expected:value<=expected;
  }
  return null;
}

export function evaluateAdditional(checks, answers={}, profile={}){
  return checks.map(check=>{
    if(check.kind==='requirement')return {...check,result:'requirement'};
    let value=answers[check.check_id],origin='answer';
    if(value===undefined&&(check.at??'application')==='application'){
      if(check.field&&profile[check.field]!==undefined){value=profile[check.field];origin='profile';}
      else if(check.profile_condition){const r=check.profile_condition,p=profile[r.field];value=compareValue(p,r);origin='profile';}
    }
    const base={...check,value,origin};
    if(value===undefined||value===null||value===''||(Array.isArray(value)&&!value.length))return {...base,result:'unknown'};
    if(check.answer_type==='source_review'){
      const recorded=typeof value==='object'?(value.choice||value.detail):String(value).trim();
      return {...base,result:recorded?'recorded':'unknown'};
    }
    if(check.answer_type==='number'&&(typeof value!=='number'||!Number.isFinite(value)||(check.min!=null&&value<check.min)||(check.max!=null&&value>check.max)||(check.step===1&&!Number.isInteger(value))))return {...base,result:'invalid'};
    if(check.answer_type==='date'&&(!/^\d{4}-\d{2}-\d{2}$/.test(value)||!Number.isFinite(Date.parse(value))||new Date(value).toISOString().slice(0,10)!==value))return {...base,result:'invalid'};
    if(check.answer_type==='boolean'&&typeof value!=='boolean')return {...base,result:'invalid'};
    if(['select','multi_select'].includes(check.answer_type)){
      const values=Array.isArray(value)?value:[value],options=check.options??[];
      if(values.some(v=>!options.some(o=>(typeof o==='object'?o.value:o)===v)))return {...base,result:'invalid'};
      if(values.some(v=>(check.uncertain_values??[]).includes(v)))return {...base,result:'unknown'};
    }
    const passed=compareValue(value,check.accepted_when);
    return {...base,result:passed===null?'recorded':passed?'pass':check.unmatched_result==='unknown'?'unknown':check.exception?'exception':'fail'};
  });
}
