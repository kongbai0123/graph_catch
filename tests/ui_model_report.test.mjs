import test from 'node:test';
import assert from 'node:assert/strict';
import {matchDetections} from '../web/model-report.mjs';
const box=s=>[s.x,s.y,s.width,s.height];
const iou=(a,b)=>{const n=Math.max(0,Math.min(a[0]+a[2],b[0]+b[2])-Math.max(a[0],b[0]))*Math.max(0,Math.min(a[1]+a[3],b[1]+b[3])-Math.max(a[1],b[1]));return n/(a[2]*a[3]+b[2]*b[3]-n)};
const shape=(label='part',confidence=.9)=>({label,x:0,y:0,width:10,height:10,confidence});
test('duplicate boxes match a truth only once and retain original prediction indices',()=>{
  const result=matchDetections({ground_truth:[shape()],shapes:[shape('part',.6),shape('part',.9)]},.5,box,iou);
  assert.deepEqual([result.tp,result.fp,result.fn],[1,1,0]);
  assert.equal(result.detections[0].index,1);assert.equal(result.detections[0].status,'TP');
});
test('wrong classes and low confidence are not counted as correct detections',()=>{
  const result=matchDetections({ground_truth:[shape()],shapes:[shape('other'),shape('part',.1)]},.5,box,iou);
  assert.deepEqual([result.tp,result.fp,result.fn],[0,1,1]);
});
test('empty predictions preserve misses and empty truth preserves false positives',()=>{
  assert.equal(matchDetections({ground_truth:[shape()]},.5,box,iou).fn,1);
  assert.equal(matchDetections({shapes:[shape()]},.5,box,iou).fp,1);
  assert.deepEqual(matchDetections({},.5,box,iou),{detections:[],missed:[],tp:0,fp:0,fn:0});
});
