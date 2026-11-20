export function buildDemoProfile({degree='phd',coursework='false',gpa='',scale='4.3',period='cumulative'}={}){
 const profile={'person.nationalities':['KR'],'education.degree':degree,'education.status':'enrolled','education.country':'KR','education.institution_id':'postech','education.department_id':'ax','education.semester':1,'education.full_time':true,'employment.status':'unemployed'};
 if(coursework==='true'||coursework==='false')profile['education.coursework_completed']=coursework==='true';
 if(gpa!=='')profile['education.gpa']={value:Number(gpa),scale:Number(scale),degree_scope:degree,period_scope:period};
 return profile;
}
