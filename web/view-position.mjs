// Hold the layout height during a synchronous replacement so scroll containers
// cannot clamp their offsets while the content is temporarily empty.
export function preserveView(root, update, {retainHeight=false}={}) {
  const positions=[];
  for(let node=root.parentElement;node;node=node.parentElement)positions.push([node,node.scrollTop,node.scrollLeft]);
  const focus=document.activeElement, chartKey=focus?.dataset?.chartGroup;
  const height=root.style.minHeight;
  root.style.minHeight=`${root.getBoundingClientRect().height}px`;
  try { update(); }
  finally {
    root.style.minHeight=retainHeight?'':height;
    if(retainHeight){
      // Reserve only the space necessary to keep the reader's viewport, rather
      // than retaining the entire height of a previously longer chart group.
      const deficit=Math.max(0,...positions.filter(([,top])=>top>0).map(([node,top])=>top+node.clientHeight-node.scrollHeight));
      if(deficit>0)root.style.minHeight=`${Math.ceil(root.getBoundingClientRect().height+deficit)}px`;
    }
    for(const [node,top,left] of positions){node.scrollTop=top;node.scrollLeft=left;}
    if(chartKey)root.querySelector(`[data-chart-group="${chartKey}"]`)?.focus({preventScroll:true});
  }
}
