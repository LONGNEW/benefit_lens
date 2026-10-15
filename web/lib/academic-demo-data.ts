import type { AcademicRuleSet } from "./academic-condition-engine";
import notices from "./academic-demo-notices.json";

export interface AcademicDemoNotice { id:string; title:string; description?:string; admissionStatus:"closed"; gradeContextLabels?:Record<string,string>; ruleSet:AcademicRuleSet; limitations?:string[]; }
export const academicDemoNotices = notices as unknown as AcademicDemoNotice[];
