import { createHash, randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdir, rename, statfs, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { gzipSync } from 'node:zlib';
import { loadBuffer } from 'cheerio';
import robotsParser from 'robots-parser';

const CACHE = '/home/longnew/.cache/datasets/benefitlens/scale50k-20261109';
const mode = process.argv[2];
const roots = new Map([
  [undefined, 'experience-university-node-pilot-v1'],
  ['--helsinki', 'experience-helsinki-node-v1'],
  ['--official-expansion', 'experience-university-official-expansion-v1'],
]);
if (!roots.has(mode)) throw new Error('Use --helsinki or --official-expansion');
const collector = roots.get(mode);
const ROOT = join(CACHE, collector);
const USER_AGENT = 'BenefitLens/1.0 (Node.js; public student-support research)';
const SOURCES = [
  {
    url: 'https://www.bath.ac.uk/case-studies/taking-on-the-challenge-of-two-different-roles/',
    markers: ['Beth Buckley', 'mock interviews'],
  },
  {
    url: 'https://www.kcl.ac.uk/political-economy/connections/study-abroad-stories',
    markers: ['Joelle', 'Kelsey'],
  },
];
const HELSINKI = [
  {
    url: 'https://studies.helsinki.fi/instructions/article/intercultural-competences',
    markers: ['Small talk confused me at first', 'Australia'],
  },
  {
    url: 'https://studies.helsinki.fi/system/files/inline-files/20260428%20Departure%20orientation%20outside%20EuropeENG_1.pdf',
    title: 'Departure orientation outside Europe, April 2026',
    markers: ['University of Otago', 'homesick'],
    pdf: true,
  },
  {
    url: 'https://www.helsinki.fi/assets/drupal/s3fs-public/migrated-degree-programme-group-pages/files/178252-2021_student_guide_book_-_final_compressed.pdf',
    title: 'Urban Studies and Planning student guidebook 2021',
    markers: ['Interview with Jalmari Sarla', 'motivation letter'],
    pdf: true,
  },
  {
    url: 'https://www.helsinki.fi/assets/drupal/2024-08/USP%20Guidebook%202024.pdf',
    title: 'Urban Studies and Planning student guidebook 2024',
    markers: ['Jalmari Sarla', 'Elina Huhtinen', 'Vancouver'],
    pdf: true,
  },
];

const OFFICIAL_EXPANSION = [
  {
    url: 'https://www.helsinki.fi/fi/koulutusohjelmat/yhteiskunnallisen-muutoksen-kandiohjelma/opiskelu/opis-ke-li-jat-esit-tay-ty-vat/ohjelmassa-painottuu-historiasta-oppiminen-nykypaivan-haasteita-varten',
    markers: ['Hertta Savela', 'Itse olin vaihdossa Pariisissa'],
  },
  {
    url: 'https://www.helsinki.fi/en/researchgroups/centre-for-nordic-studies/news-events/the-duty-free-phenomenon-caught-my-interest-from-the-very-beginning-qa-with-visiting-researcher-louice-badholm',
    markers: ['Louice Bådholm', 'Jan Wallander and Tom Hedelius Foundation'],
  },
  {
    url: 'https://www.helsinki.fi/en/news/donation/i-finally-knew-what-it-feels-when-your-dreams-come-true-donated-scholarships-support-international-students',
    markers: ['Lakshika Palamakumbure', 'offered a scholarship'],
  },
  {
    url: 'https://www.helsinki.fi/en/degree-programmes/microbiology-and-microbial-biotechnology-masters-programme/news-archive/our-studies-help-environment-and-make-sure-infections-can-be-treated-antibiotics-future-well',
    markers: ['Victor:', 'I got both, the scholarship and the study place'],
  },
  {
    url: 'https://www.bath.ac.uk/case-studies/manufacturing-the-future-learning-ai-through-an-engineering-lens-on-placement/',
    markers: ['Mia', 'what I submitted and the response'],
  },
  {
    url: 'https://www.kcl.ac.uk/people/sara-saxena',
    markers: ['Sara Saxena', 'Vice Chancellor’s Scholarship'],
  },
];

async function read(url, expectedType, maxBytes) {
  const response = await fetch(url, {
    headers: { 'User-Agent': USER_AGENT, Accept: expectedType },
    redirect: 'manual',
    signal: AbortSignal.timeout(20_000),
  });
  const contentType = response.headers.get('content-type')?.split(';')[0].trim();
  if (response.status !== 200 || contentType !== expectedType || !response.body) {
    await response.body?.cancel();
    throw new Error(`HTTP ${response.status}, ${contentType ?? 'no content type'}`);
  }
  const chunks = [];
  let bytes = 0;
  for await (const chunk of response.body) {
    bytes += chunk.byteLength;
    if (bytes > maxBytes) throw new Error('Response exceeds size limit');
    chunks.push(chunk);
  }
  return Buffer.concat(chunks, bytes);
}

async function save(body, suffix) {
  const sha256 = createHash('sha256').update(body).digest('hex');
  const path = join(ROOT, `${sha256}.${suffix}.gz`);
  try {
    await writeFile(path, gzipSync(body), { flag: 'wx' });
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
  }
  return { path, sha256, decoded_bytes: body.length };
}

function extract(html, markers) {
  const $ = loadBuffer(html, { encoding: { defaultEncoding: 'utf-8' } });
  const main = $('main, [role=main], #main').first();
  main.find('script, style, nav, footer, noscript').remove();
  // A page can contain nested article and aside wrappers. Capture the semantic
  // text blocks once without losing their headings or paragraph boundaries.
  const blocks = 'h1, h2, h3, h4, h5, h6, p, li, blockquote, figcaption, hy-ingress';
  const text = main.find(blocks).toArray()
    .filter(node => !$(node).find(blocks).length)
    .map(node => $(node).text().replace(/\s+/g, ' ').trim())
    .filter(Boolean).join('\n');
  if (text.length < 200 || !markers.every(marker => text.includes(marker))) {
    throw new Error('Expected article content missing');
  }
  return { title: $('head > title').first().text().trim(), text };
}

async function collect({ url, markers, title: sourceTitle, pdf = false }) {
  const result = { url, accepted_experience: false };
  let stage = 'robots';
  try {
    const robotsUrl = new URL('/robots.txt', url).href;
    const bytes = await read(robotsUrl, 'text/plain', 512 * 1024);
    const policyText = bytes.toString('utf8').replace(/^\uFEFF/, '');
    if (/^\s*</.test(policyText)) throw new Error('Invalid robots response');
    const policy = robotsParser(robotsUrl, policyText);
    if (policy.isAllowed(url, USER_AGENT) !== true) {
      throw new Error('robots policy does not allow this article');
    }
    result.robots = { url: robotsUrl, ...(await save(bytes, 'robots.txt')) };
    const interval = Math.max(2, policy.getCrawlDelay(USER_AGENT) ?? 0);
    if (interval > 30) throw new Error('Crawl delay exceeds this pilot window');
    await delay(interval * 1000);
    stage = 'article';
    const original = await read(url, pdf ? 'application/pdf' : 'text/html',
      (pdf ? 32 : 5) * 1024 * 1024);
    result.original = await save(original, pdf ? 'pdf' : 'html');
    const { title, text } = pdf
      ? { title: sourceTitle, text: execFileSync('pdftotext', ['-', '-'], {
          input: original, encoding: 'utf8', timeout: 20_000, maxBuffer: 8 * 1024 * 1024,
        }) }
      : extract(original, markers);
    if (pdf && !markers.every(marker => text.replace(/\s+/g, ' ').includes(marker))) {
      throw new Error('Expected interview content missing from PDF text');
    }
    result.title = title;
    result.text = await save(Buffer.from(text), 'txt');
    result.text_characters = text.length;
    result.state = 'body_staged';
  } catch (error) {
    Object.assign(result, { state: 'held', failed_at: stage, reason: error.message });
  }
  return result;
}

const disk = await statfs(CACHE, { bigint: true });
if (disk.bsize * disk.bavail < 256n * 1024n * 1024n) {
  throw new Error('Shared cache has less than 256 MiB available');
}
await mkdir(ROOT, { recursive: true });
const groups = new Map();
const selected = mode === '--helsinki' ? HELSINKI
  : mode === '--official-expansion' ? OFFICIAL_EXPANSION : SOURCES;
for (const source of selected) {
  const host = new URL(source.url).host;
  if (!groups.has(host)) groups.set(host, []);
  groups.get(host).push(source);
}
const pages = (await Promise.all([...groups.values()].map(async sources => {
  const results = [];
  for (const source of sources) {
    if (results.length) await delay(2000);
    const page = await collect(source);
    results.push(page);
    if (page.state === 'held' && /HTTP (401|403|429)\b/.test(page.reason)) break;
  }
  return results;
}))).flat();
const summary = {
  at: new Date().toISOString(), collector,
  node_version: process.version, user_agent: USER_AGENT,
  staged_pages: pages.filter(page => page.state === 'body_staged').length,
  accepted_experiences_added: 0, pages,
};
const temporary = join(ROOT, `status-${randomUUID()}.tmp`);
await writeFile(temporary, JSON.stringify(summary, null, 2) + '\n');
await rename(temporary, join(ROOT, 'status.json'));
console.log(JSON.stringify(summary, null, 2));
if (summary.staged_pages !== selected.length) process.exitCode = 1;
