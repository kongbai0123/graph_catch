// Estimates use measured progress only, never a timer-generated percentage.
export function duration(seconds) {
  const value=Math.max(0,Math.ceil(seconds));
  if(value<60)return `${value} 秒`;
  if(value<3600)return `${Math.floor(value/60)} 分 ${value%60} 秒`;
  return `${Math.floor(value/3600)} 小時 ${Math.floor(value%3600/60)} 分`;
}
export function countdown(seconds) {
  const value=Math.max(0,Math.ceil(Number(seconds)||0));
  const hours=Math.floor(value/3600),minutes=Math.floor(value%3600/60),secs=value%60;
  return `${String(hours).padStart(2,'0')}:${String(minutes).padStart(2,'0')}:${String(secs).padStart(2,'0')}`;
}
export function taskStage(message='正在處理') {
  return String(message).replace(/\s*\d+\s*\/\s*\d+/g,'').replace(/第\s*\d+\s*(?:幀|張|筆|項)/g,'').replace(/\s*[·（(]?已(?:耗時|進行)\s*[\d.]+\s*秒[）)]?/g,'').trim();
}
export class TaskTiming {
  constructor(clock=()=>performance.now()/1000){this.clock=clock;this.started=clock();this.last=this.started;this.elapsed=0;this.samples=[];this.state='queued';this.phase=null;}
  update(progress,state='running',phase='default'){
    const now=this.clock();
    if(this.state==='running')this.elapsed+=Math.max(0,now-this.last);
    this.last=now;
    if(state!==this.state||phase!==this.phase)this.samples=[];
    this.state=state;this.phase=phase;
    if(state!=='running'||!Number.isFinite(progress)){this.samples=[];return this;}
    const previous=this.samples.at(-1);
    const span=previous?previous.t-this.samples[0].t:0;
    if(previous&&progress>previous.p&&now-previous.t>Math.max(30,span/Math.max(1,this.samples.length-1)*3))this.samples=[];
    if(previous&&progress<previous.p)this.samples=[];
    if(!this.samples.length||progress>this.samples.at(-1).p){
      this.samples.push({t:now,p:progress});
      this.samples=this.samples.slice(-12);
    }
    return this;
  }
  text(scope='目前階段'){
    const now=this.clock();
    if(['succeeded','completed','complete'].includes(this.state))return '已完成';
    if(this.state==='failed')return '處理失敗';
    if(['cancelled','stopped'].includes(this.state))return '已停止';
    if(this.state==='queued')return '進度 0% · 剩餘時間計算中';
    if(this.state==='paused')return '已暫停 · 剩餘時間暫停更新';
    if(this.state==='stopping')return '正在安全停止';
    if(!this.samples.length)return '進度 0% · 剩餘時間計算中';
    const first=this.samples[0],last=this.samples.at(-1),span=last.t-first.t;
    if(last.p>=100)return '正在確認完成';
    const progress=Math.max(0,Math.min(100,last.p));
    if(this.samples.length<2||span<=0||last.p<=first.p)return `進度 ${Math.round(progress)}% · 剩餘時間計算中`;
    // Include time since the last progress event so a stalled task increases its
    // ETA instead of displaying an expired countdown.
    const measuredSpan=Math.max(span,now-first.t),remaining=(100-last.p)*measuredSpan/(last.p-first.p);
    return `進度 ${Math.round(progress)}% · ${scope}剩餘 ${countdown(Math.max(1,remaining))}`;
  }
}
