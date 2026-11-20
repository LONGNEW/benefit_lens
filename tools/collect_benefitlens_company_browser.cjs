const {chromium} = require(process.env.BENEFITLENS_PLAYWRIGHT || 'playwright');
const sources = {
    nhr: ['https://careers.nhr.kr/jobs', /^\/jobs\/RC\d+$/i, 'a[href^="/jobs/RC"]', /HR마케팅|Platform Operations|채용대행/],
    lotte: ['https://recruit.lotte.co.kr/apply/announcement/list', /^\/apply\/announcement\/detail\/\d+$/, 'a[href*="/apply/announcement/detail"]', /인턴|intern/i],
    gabia: ['https://recruit.gabia.com/recruit/jobs', /^\/recruit\/view\/[a-z0-9]+$/, 'a[href*="/recruit/view/"]', /채용연계형/],
    wins: ['https://wins21.recruiter.co.kr/career/jobs', /^\/career\/jobs\/\d+$/, 'a[href*="/career/jobs/"]', /인턴|실습/],
    modoodoc: ['https://modoodoc.career.greetinghr.com/ko/home', /^\/ko\/o\/\d+$/, 'a[href*="/ko/o/"]', /인턴|intern/i],
    ey: ['https://eycareers-kr.recruiter.co.kr/career/jobs', /^\/career\/jobs\/\d+$/, 'a[href*="/career/jobs/"]', /인턴|intern/i]
};
const expectedFor = {nhr:/지원자격|지원 자격|지원하기/,lotte:/접수방법|지원하기/,gabia:/접수|지원/,wins:/지원|모집/,modoodoc:/지원하기|마감기한/,ey:/지원하기|마감일/};
const extra = {lotte:['https://recruit.lotte.co.kr/apply/announcement/detail/22001699'],wins:['https://wins21.recruiter.co.kr/career/jobs/133003'],modoodoc:['https://modoodoc.career.greetinghr.com/ko/o/237550'],ey:['https://eycareers-kr.recruiter.co.kr/career/jobs/133752','https://eycareers-kr.recruiter.co.kr/career/jobs/133908']};
const maximum = 30;
const selected = process.argv.slice(2);
if (selected.some(key => !Object.hasOwn(sources,key))) throw new Error('Unknown source ID');

async function load(page, url, wait) {
    const response = await page.goto(url, {waitUntil:'domcontentloaded', timeout:25000});
    if (!response || response.status() !== 200) throw Error("HTTP " + response?.status());
    if (new URL(page.url()).origin !== new URL(url).origin) throw Error("cross-origin redirect");
    if (wait) {
        await page.locator("body").filter({hasText:wait}).first().waitFor({state:"attached",timeout:15000});
    }
    const html = await page.content();
    const contents = await page.locator('body').textContent();
    return {url, finalUrl:page.url(), status:response.status(), at:new Date().toISOString(), html, contents};
}

async function collect(browser, key) {
    const [href, pattern, selector, relevant] = sources[key];
    const context = await browser.newContext({locale:'ko-KR'});
    const page = await context.newPage();
    const result = {source:key, url:href, records:[]};
    try {
        result.listing = await load(page,href,key==='nhr'? /총 [0-9]+건의 공고|채용대행_인턴/ : /채용공고|모집공고|모집 공고|채용 공고/);
        await page.locator(selector).first().waitFor({timeout:3500}).catch(()=>{});
        const cards = await page.locator('a[href]').evaluateAll(xs=>xs.map(x=>[x.href,x.innerText.trim()]));
        const used = new Set();
        result.observed = cards.filter(([u])=> {
            try {const x = new URL(u);return x.origin===new URL(href).origin&&pattern.test(x.pathname);} catch {return false;}
        }).filter(([u])=>{if(used.has(u))return false;used.add(u);return true;});
    } catch (error) {result.error=error.message; result.observed=[];}
    const next = new Map(result.observed.filter(([,text])=>relevant.test(text)));
    for (const u of extra[key]||[]) if (!next.has(u)) next.set(u,'');
    for (const [u, title] of Array.from(next).slice(0,maximum)) {
        await page.waitForTimeout(2000);
        try {result.records.push({...await load(page,u,expectedFor[key]),title});}
        catch (error) {result.records.push({url:u,title,error:error.message});}
    }
    await context.close();
    return result;
}
(async () => {
    const browser=await chromium.launch({executablePath:process.env.BENEFITLENS_CHROME || '/opt/google/chrome/chrome',headless:true});
    try {
        const results=await Promise.all((selected.length ? selected : Object.keys(sources)).map(key=>collect(browser,key)));
        for (const row of results) process.stdout.write(JSON.stringify(row)+'\n');
    } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
