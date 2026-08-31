from __future__ import annotations
import html, json
from pathlib import Path
ROOT=Path(__file__).resolve().parent; DATA=ROOT/'data'

def load(name,default):
 p=DATA/name
 try:return json.loads(p.read_text(encoding='utf-8'))
 except:return default

def esc(v):return html.escape(str(v if v is not None else ''))
def badge(risk):
 s=str(risk or '').upper(); cls='good' if s in {'COMFORTABLE','FAVORED'} else 'warn' if s in {'TOSS-UP','PREDRAFT','LEAN YOU'} else 'bad'
 return f'<span class="badge {cls}">{esc(s)}</span>'
def main():
 cc=load('command_center.json',{'leagues':[]}); trades=load('trade_targets.json',{'leagues':{}}); rip=load('injury_ripple_summary.json',{}); weather=load('weather_summary.json',{}); ol=load('ol_health_summary.json',{})
 cards=[]
 for l in cc.get('leagues',[]):
  lid=str(l.get('league_id')); t=(trades.get('leagues') or {}).get(lid,{})
  push=(t.get('push_targets') or [])[:3]; rebuild=(t.get('rebuild_targets') or [])[:3]
  trade_html=''.join(f'<li><b>{esc(x.get("player"))}</b> · {esc(x.get("position"))} · from {esc(x.get("seller"))}</li>' for x in push) or '<li>None yet</li>'
  rebuild_html=''.join(f'<li><b>{esc(x.get("player"))}</b> · {esc(x.get("position"))} · age {esc(x.get("age"))}</li>' for x in rebuild) or '<li>None yet</li>'
  finish=''
  if str(l.get('format'))!='chopped':
   if l.get('projected_seed'):
    finish=f'''<div class="finish"><div><span>Projected seed</span><strong>#{esc(l.get('projected_seed'))}</strong></div><div><span>Expected wins</span><strong>{esc(l.get('expected_wins'))}</strong></div><div><span>Playoff odds</span><strong>{esc(l.get('playoff_odds'))}%</strong></div><div><span>Likely range</span><strong>{esc(l.get('finish_range'))}</strong></div></div><div class="finishnote">Roster power #{esc(l.get('roster_power_rank'))} · schedule {esc(l.get('schedule_label'))} ({esc(l.get('schedule_difficulty_percentile'))}th percentile difficulty)</div>'''
   elif str(l.get('risk'))=='PREDRAFT':
    finish='<div class="finishnote">Projected finish activates after the draft creates real rosters.</div>'
  cards.append(f'''<section class="card"><div class="cardtop"><div><h2>{esc(l.get('league'))}</h2><div class="sub">{esc(l.get('team'))}</div></div>{badge(l.get('risk'))}</div>
  <div class="metrics"><div><span>Projection</span><strong>{esc(l.get('my_projection') or '—')}</strong></div><div><span>Margin</span><strong>{esc(l.get('margin') or '—')}</strong></div><div><span>Injury monitors</span><strong>{esc(l.get('injury_monitors'))}</strong></div></div>
  {finish}
  <div class="action"><b>{esc(l.get('top_action'))}</b> · {esc(l.get('action_detail'))}</div>
  <div class="grid2"><div><h3>Waiver</h3><p><b>{esc(l.get('top_waiver') or '—')}</b> {esc(l.get('waiver_position'))}<br>{esc(l.get('waiver_priority'))} · FAAB {esc(l.get('faab'))}</p></div><div><h3>Trade posture</h3><p>{esc(t.get('posture') or 'Not active yet')}</p></div></div>
  <div class="grid2"><div><h3>Push targets</h3><ul>{trade_html}</ul></div><div><h3>Rebuild targets</h3><ul>{rebuild_html}</ul></div></div></section>''')
 summary=f'''<section class="summary"><div><span>Projection mode</span><strong>{esc(cc.get('projection_mode'))}</strong></div><div><span>Usage</span><strong>{esc(cc.get('usage_mode'))}</strong></div><div><span>Ripple players</span><strong>{esc(rip.get('players_affected',0))}</strong></div><div><span>OL mapping</span><strong>{round(float(ol.get('mapping_rate',0))*100,1) if ol else 0}%</strong></div><div><span>Outdoor forecasts</span><strong>{esc(weather.get('forecasted_games',0))}</strong></div></section>'''
 css='''body{margin:0;background:#0b1020;color:#edf2ff;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif}.wrap{max-width:1280px;margin:auto;padding:24px}h1{font-size:30px;margin:0 0 4px}.muted,.sub{color:#95a2bd}.summary{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:22px 0}.summary div,.card{background:#141b2f;border:1px solid #26304a;border-radius:16px}.summary div{padding:14px}.summary span,.metrics span,.finish span{display:block;color:#94a2bd;font-size:12px;text-transform:uppercase;letter-spacing:.06em}.summary strong{font-size:16px}.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.card{padding:18px}.cardtop{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.card h2{margin:0;font-size:21px}.badge{padding:6px 10px;border-radius:999px;font-size:12px;font-weight:800}.good{background:#173f31;color:#8ff0bd}.warn{background:#4a3b14;color:#ffd777}.bad{background:#4d2026;color:#ff9da8}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:16px 0}.metrics div,.finish div{background:#0f1526;border-radius:11px;padding:10px}.metrics strong{font-size:20px}.finish{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 8px}.finish strong{font-size:18px}.finishnote{font-size:13px;color:#aeb9cf;background:#11182a;border-radius:10px;padding:9px 11px;margin-bottom:14px}.action{background:#202943;border-radius:11px;padding:12px;margin-bottom:14px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}.grid2>div{background:#0f1526;border-radius:11px;padding:12px}h3{font-size:13px;color:#9fb0d3;text-transform:uppercase;margin:0 0 8px}p,li{font-size:14px;line-height:1.45}ul{margin:0;padding-left:18px}@media(max-width:850px){.summary{grid-template-columns:1fr 1fr}.cards{grid-template-columns:1fr}.grid2{grid-template-columns:1fr}.finish{grid-template-columns:1fr 1fr}}'''
 doc=f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Fantasy War Room</title><style>{css}</style></head><body><div class="wrap"><h1>Fantasy War Room · Week {esc(cc.get('week'))}</h1><div class="muted">Updated {esc(cc.get('snapshot_utc'))}</div>{summary}<div class="cards">{''.join(cards)}</div></div></body></html>'''
 (DATA/'war_room.html').write_text(doc,encoding='utf-8')
 md=[f"# Fantasy War Room — Week {cc.get('week')}","",f"Projection mode: **{cc.get('projection_mode')}** · Usage: **{cc.get('usage_mode')}**",""]
 for l in cc.get('leagues',[]):
  t=(trades.get('leagues') or {}).get(str(l.get('league_id')),{})
  md += [f"## {l.get('league')}",f"- {l.get('risk')} — projection {l.get('my_projection') or '—'} · margin {l.get('margin') or '—'}"]
  if l.get('projected_seed') and str(l.get('format'))!='chopped':md.append(f"- Projected finish: seed #{l.get('projected_seed')} · {l.get('expected_wins')} wins · {l.get('playoff_odds')}% playoffs · range {l.get('finish_range')} · roster power #{l.get('roster_power_rank')} · schedule {l.get('schedule_label')}")
  md += [f"- Action: {l.get('top_action')} — {l.get('action_detail')}",f"- Waiver: {l.get('top_waiver') or '—'} · {l.get('waiver_priority') or ''} · FAAB {l.get('faab') or '—'}",f"- Trade posture: {t.get('posture','Not active yet')}",f"- Push targets: "+(', '.join(x.get('player') for x in (t.get('push_targets') or [])[:5]) or 'none'),f"- Rebuild targets: "+(', '.join(x.get('player') for x in (t.get('rebuild_targets') or [])[:5]) or 'none'),""]
 (DATA/'war_room.md').write_text('\n'.join(md),encoding='utf-8')
 print(json.dumps({'war_room_html':True,'league_cards':len(cards)},indent=2))
if __name__=='__main__': main()
