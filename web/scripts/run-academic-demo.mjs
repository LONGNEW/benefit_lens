#!/usr/bin/env node
// Local-only preview of the academic demo. Never opens the operational DB.
import { cp, mkdir, mkdtemp, readFile, readdir, symlink, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
const args=process.argv.slice(2), options={port:5178};
for(let i=0;i<args.length;i++) {
 if(args[i]==='--prepare-only'){options.prepareOnly=true;continue;}
 if(!['--port','--workspace','--project-root'].includes(args[i])||!args[i+1])throw new Error('Usage: node run-academic-demo.mjs [--port 5178] [--workspace DIR] [--project-root PROJECT] [--prepare-only]');
 options[args[i].slice(2)]=args[++i];
}
const major=Number(process.versions.node.split('.')[0]);
if(major<22)throw new Error('This installed Next.js preview requires Node.js 22 or newer. No runtime is installed by this script.');
const project=options['project-root']?resolve(options['project-root']):resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const web=join(project,'web'), port=Number(options.port);
if(!Number.isInteger(port)||port<1024||port>65535)throw new Error('Invalid local port');
const parent=options.workspace?resolve(options.workspace):tmpdir();await mkdir(parent,{recursive:true});
const staged=await mkdtemp(join(parent,'benefitlens-academic-'));
const files=['package.json','tsconfig.json','next-env.d.ts','next.config.ts','postcss.config.mjs','app/layout.tsx','app/globals.css','app/page.tsx',
 'app/academic/page.tsx','app/academic/academic.module.css','app/academic/writing-panel.tsx','lib/academic-condition-engine.ts','lib/academic-demo-data.ts','lib/academic-demo-notices.json','lib/draft.ts','lib/draft-edit-state.ts','lib/writing-resources.json'];
const records=[];
for(const relative of files){const source=join(web,relative),dest=join(staged,relative);await mkdir(dirname(dest),{recursive:true});await cp(source,dest);const bytes=await readFile(dest);records.push({path:relative,sha256:createHash('sha256').update(bytes).digest('hex')});}
for(const dir of ['public','vendor'])if(existsSync(join(web,dir)))await cp(join(web,dir),join(staged,dir),{recursive:true});
const dependencyDir=join(staged,'node_modules');await mkdir(dependencyDir);
for(const entry of await readdir(join(web,'node_modules'),{withFileTypes:true})){
 if(['.vite','.vite-temp'].includes(entry.name))continue;
 await symlink(join(web,'node_modules',entry.name),join(dependencyDir,entry.name),entry.isDirectory()?'dir':'file');
}
await mkdir(join(dependencyDir,'.vite-temp'));
const report={source_project:project,preview_directory:staged,url:`http://127.0.0.1:${port}/academic`,node_version:process.version,files:records,operational_db_connected:false,telemetry_disabled:true,external_deployment:false};
await writeFile(join(staged,'academic-preview-manifest.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));
if(options.prepareOnly)process.exit(0);
const child=spawn(process.execPath,[join(dependencyDir,'next/dist/bin/next'),'dev','--hostname','127.0.0.1','--port',String(port),'--webpack'],{cwd:staged,stdio:'inherit',env:{...process.env,NEXT_TELEMETRY_DISABLED:'1'}});
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>child.kill(signal));
child.on('exit',(code)=>process.exit(code??1));
child.on('error',error=>{console.error(error.message);process.exit(1);});
