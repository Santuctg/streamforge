#!/usr/bin/env python3
from pathlib import Path

MARKER = 'STREAMFORGE_ADAPTIVE_DASHBOARD_DATETIME_V1244'
HELPERS = """/* STREAMFORGE_ADAPTIVE_DASHBOARD_DATETIME_V1244 */
  const formatDashboardAxisTime=(date,spanMs)=>{const time=date.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});if(spanMs<=6*60*60*1000)return [time];const datePart=date.toLocaleDateString([],{day:'2-digit',month:'short',...(spanMs>31*24*60*60*1000?{year:'numeric'}:{})});if(spanMs<=48*60*60*1000)return [datePart,time];return [datePart]};
  const formatDashboardTooltipTime=date=>date.toLocaleString([],{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'});
"""
NODE_HELPERS = """/* STREAMFORGE_ADAPTIVE_DASHBOARD_DATETIME_V1244 */const formatDashboardAxisTime=(date,spanMs)=>{const time=date.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});if(spanMs<=6*60*60*1000)return [time];const datePart=date.toLocaleDateString([],{day:'2-digit',month:'short',...(spanMs>31*24*60*60*1000?{year:'numeric'}:{})});if(spanMs<=48*60*60*1000)return [datePart,time];return [datePart]};const formatDashboardTooltipTime=date=>date.toLocaleString([],{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'});"""


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)

# Main dashboard
main_path = Path('app/static/dashboard.js')
main = main_path.read_text(encoding='utf-8')
if MARKER not in main:
    main = replace_once(
        main,
        "  const root=document.querySelector('[data-metrics-history]'); if(!root) return;\n",
        "  const root=document.querySelector('[data-metrics-history]'); if(!root) return;\n" + HELPERS,
        'Main helper insertion',
    )
    main = replace_once(main, 'R=18,T=18,B=42,pw=', 'R=18,T=18,B=54,pw=', 'Main bottom margin')
    old_tick = "const ticks=Math.min(w>=1000?7:5,count);for(let n=0;n<ticks;n++){const i=Math.round(n*(count-1)/Math.max(1,ticks-1)),x=tx(i),d=new Date(Number(points[i]?.time||0)*1000),label=d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});c.fillStyle='#9fb0c3';c.fillText(label,Math.max(L,x-18),h-12)}"
    new_tick = "const spanMs=count>1?Math.max(0,(Number(points[count-1]?.time||0)-Number(points[0]?.time||0))*1000):0;const ticks=Math.min(w>=1000?7:5,count);for(let n=0;n<ticks;n++){const i=Math.round(n*(count-1)/Math.max(1,ticks-1)),x=tx(i),d=new Date(Number(points[i]?.time||0)*1000),labels=formatDashboardAxisTime(d,spanMs);c.fillStyle='#9fb0c3';labels.forEach((label,row)=>c.fillText(label,Math.max(L,x-(label.length>8?28:18)),h-12-(labels.length-1-row)*15))}"
    main = replace_once(main, old_tick, new_tick, 'Main adaptive ticks')
    main = replace_once(main, 'const boxW=190,boxH=', 'const boxW=250,boxH=', 'Main tooltip width')
    main = replace_once(main, "c.fillText(new Date(Number(points[hover].time)*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}),bx+10,by+19)", "c.fillText(formatDashboardTooltipTime(new Date(Number(points[hover].time)*1000)),bx+10,by+19)", 'Main full tooltip timestamp')
    main_path.write_text(main, encoding='utf-8')

# Node dashboard embedded JS
node_path = Path('node_agent/app.py')
node = node_path.read_text(encoding='utf-8')
if MARKER not in node:
    node = replace_once(node, 'const syncNodeCanvasSize=(canvas)=>', NODE_HELPERS + 'const syncNodeCanvasSize=(canvas)=>', 'Node helper insertion')
    node = replace_once(node, 'R=18,T=18,B=42,pw=', 'R=18,T=18,B=54,pw=', 'Node bottom margin')
    old_tick = "const ticks=Math.min(w>=1000?7:5,count);for(let n=0;n<ticks;n++){const i=Math.round(n*(count-1)/Math.max(1,ticks-1)),x=tx(i),d=new Date(Number(points[i]?.time||0)*1000);c.fillStyle='#9fb0c3';c.fillText(d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}),Math.max(L,x-18),h-12)}"
    new_tick = "const spanMs=count>1?Math.max(0,(Number(points[count-1]?.time||0)-Number(points[0]?.time||0))*1000):0;const ticks=Math.min(w>=1000?7:5,count);for(let n=0;n<ticks;n++){const i=Math.round(n*(count-1)/Math.max(1,ticks-1)),x=tx(i),d=new Date(Number(points[i]?.time||0)*1000),labels=formatDashboardAxisTime(d,spanMs);c.fillStyle='#9fb0c3';labels.forEach((label,row)=>c.fillText(label,Math.max(L,x-(label.length>8?28:18)),h-12-(labels.length-1-row)*15))}"
    node = replace_once(node, old_tick, new_tick, 'Node adaptive ticks')
    node = replace_once(node, 'const bw=190,bh=', 'const bw=250,bh=', 'Node tooltip width')
    node = replace_once(node, "c.fillText(new Date(Number(points[hover].time)*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}),bx+10,by+19)", "c.fillText(formatDashboardTooltipTime(new Date(Number(points[hover].time)*1000)),bx+10,by+19)", 'Node full tooltip timestamp')
    node_path.write_text(node, encoding='utf-8')

print('v12.44 Main + Node dashboard adaptive date/time materialized')
