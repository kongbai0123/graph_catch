const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
export function downloadReport(name,data,type='application/json'){
  const url=URL.createObjectURL(data instanceof Blob?data:new Blob([typeof data==='string'?data:JSON.stringify(data,null,2)],{type}));
  const link=el('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export async function downloadBatch(name,rows,session,box,caption){
  if(!rows.length)return;
  const canvas=document.createElement('canvas');canvas.width=1280;canvas.height=70+rows.length*410;
  const ctx=canvas.getContext('2d');ctx.fillStyle='#101d25';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.fillStyle='#fff';ctx.font='18px sans-serif';ctx.fillText(caption,20,36);
  for(const [i,row] of rows.entries()){
    const image=new Image();image.src=`/api/model-trials/${session}/frames/${row.index}`;await image.decode();
    const y=70+i*410;ctx.fillStyle='#fff';ctx.font='16px sans-serif';ctx.fillText(`Image ${row.index+1} | TP ${row.tp} / FP ${row.fp} / FN ${row.fn} | Ground Truth (left) / Prediction + FN (right)`,20,y+20);
    for(const truth of [true,false]){
      const x=truth?20:650,scale=Math.min(610/row.frame.width,350/row.frame.height);ctx.drawImage(image,x,y+36,row.frame.width*scale,row.frame.height*scale);
      const shapes=truth?(row.frame.ground_truth||[]).map(shape=>({shape,color:'#65a9ff',status:'GT'})):[...row.detections.map(d=>({...d,color:d.status==='TP'?'#45c6b1':'#ff677c'})),...row.missed.map(d=>({...d,color:'#ffba59',status:'FN'}))];
      for(const {shape,color,status} of shapes){const [left,top,w,h]=box(shape,row.frame);ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=2;ctx.strokeRect(x+left*scale,y+36+top*scale,w*scale,h*scale);ctx.font='13px sans-serif';ctx.fillText(`${status} ${shape.label}`,x+left*scale,y+36+Math.max(14,top*scale));}
    }
  }
  const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)throw Error('無法產生 Batch 圖片');downloadReport(name,blob);
}
const names={images:'評估圖片',box_map50_95:'Box mAP50–95',box_map50:'Box mAP50',box_map75:'Box mAP75',box_precision_best_f1:'Box Precision（最佳 F1）',box_recall_best_f1:'Box Recall（最佳 F1）',mask_map50_95:'Mask mAP50–95',mask_map50:'Mask mAP50',mask_map75:'Mask mAP75',mask_precision_best_f1:'Mask Precision（最佳 F1）',mask_recall_best_f1:'Mask Recall（最佳 F1）',accuracy:'Accuracy',macro_f1:'Macro F1',macro_recall:'Macro Recall',mean_iou:'Mean IoU',mean_dice:'Dice',precision_50:'Precision@0.5',recall_50:'Recall@0.5'};
export function evaluationReport(model){
  const root=el('div',undefined,'model-evaluation-report'),toolbar=el('div',undefined,'report-toolbar'),select=el('select');select.setAttribute('aria-label','評估資料集合');
  for(const [key,label] of [['test','Test'],['validation','Validation']]){const option=el('option',label);option.value=key;select.append(option)}
  select.value=model.test?'test':'validation';const output=el('div');
  const save=el('button','下載評估 JSON','secondary');save.type='button';save.onclick=()=>downloadReport(`${model.model_version_id}-evaluation.json`,{model_version_id:model.model_version_id,run_id:model.run_id,dataset_version_id:model.dataset_version_id,classes:model.classes,protocol:model.evaluation_protocol,reassessment:model.evaluation_reassessment,validation:model.validation,test:model.test});toolbar.append(select,save);root.append(toolbar,output);
  const render=()=>{output.replaceChildren();if(model.evaluation_reassessment?.valid===false){output.append(el('p',model.evaluation_reassessment.reason||'此模型的歷史評估不可用。','readiness-item warning'));return}
    const result=model[select.value];if(!result){output.append(el('p','此模型沒有這個集合的評估紀錄。','report-empty'));return}
    const metrics=el('div',undefined,'report-score-grid');for(const [key,label] of Object.entries(names)){const value=result[key];if(typeof value!=='number'||!Number.isFinite(value))continue;const card=el('div',undefined,'run-metric');card.append(el('span',label),el('b',key==='images'?String(value):value.toFixed(3)));metrics.append(card)}output.append(metrics);
    if(model.evaluation_protocol?.test_independent_sources===false)output.append(el('p','Train／Validation／Test 含相同來源；此結果不代表獨立新場景的泛化表現。','readiness-item warning'));
    output.append(el('p','此處顯示訓練完成時保存的評估。辨識對照中的 TP／FP／FN 依目前信心門檻重新計算。','field-note'));
    if(result.per_class){const wrap=el('div',undefined,'report-table-scroll'),table=el('table',undefined,'model-parameter-table'),entries=Object.entries(result.per_class),keys=[...new Set(entries.flatMap(([,v])=>Object.keys(v).filter(k=>typeof v[k]==='number'||v[k]===null)))];const head=el('tr');for(const label of ['類別',...keys])head.append(el('th',label));table.append(head);for(const [label,values] of entries){const row=el('tr');row.append(el('td',label));for(const key of keys)row.append(el('td',values[key]==null?'—':String(values[key])));table.append(row)}wrap.append(table);output.append(wrap)}
    if(Array.isArray(result.confusion_matrix)&&result.confusion_matrix.every(Array.isArray)){
      const labels=result.classes||model.classes||[],wrap=el('div',undefined,'report-table-scroll'),table=el('table',undefined,'model-parameter-table'),head=el('tr');
      for(const label of ['真實／預測',...labels])head.append(el('th',label));table.append(head);
      result.confusion_matrix.forEach((values,i)=>{const row=el('tr');row.append(el('th',labels[i]??String(i)));for(const value of values)row.append(el('td',String(value)));table.append(row)});wrap.append(table);output.append(el('h3','混淆矩陣'),wrap);
    }
    if(!result.per_class)output.append(el('p','此紀錄未保存逐類別明細，可至「辨識對照」執行固定資料評估。','field-note'));
  };select.onchange=render;render();return root;
}

export function matchDetections(frame,threshold,box,iou,iouThreshold=.5){
  const truth=frame.ground_truth||[],predictions=(frame.shapes||[]).map((shape,index)=>({shape,index,confidence:Number(shape.metadata?.confidence??shape.confidence??1)})).filter(p=>p.confidence>=threshold).sort((a,b)=>b.confidence-a.confidence),used=new Set();
  const detections=predictions.map(p=>{let match=-1,overlap=0;truth.forEach((t,index)=>{if(used.has(index)||t.label!==p.shape.label)return;const value=iou(box(t,frame),box(p.shape,frame));if(value>=iouThreshold&&value>overlap){match=index;overlap=value}});if(match>=0)used.add(match);return {...p,status:match>=0?'TP':'FP',truth_index:match,iou:match>=0?overlap:null}});
  return {detections,missed:truth.map((shape,index)=>({shape,index})).filter(t=>!used.has(t.index)),tp:used.size,fp:detections.length-used.size,fn:truth.length-used.size};
}
