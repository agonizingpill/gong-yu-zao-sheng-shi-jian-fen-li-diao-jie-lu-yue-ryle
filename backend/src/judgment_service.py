from sqlalchemy import select
from .models import Agreement,NoiseEvent,Separation,Remedy,Breach,Judgment
from .common import require,lock_agreement,get,record,dump,now,within_window

# 复发判定只针对在途协议；晚到与结案前补录的事件按采集时刻进入同一判定。
SCOPED_STATES=('active','observing','breached')

def covering_separation(db,complaint_id,event_id):
    jobs=db.scalars(select(Separation).where(Separation.complaint_id==complaint_id,Separation.state=='done').order_by(Separation.id.desc())).all()
    return next((s for s in jobs if event_id in (s.event_ids or [])),None)
def event_weights(separation,event_id):
    for e in (separation.result or {}).get('events',[]):
        if e.get('event_id')==event_id:return e.get('weights') or {}
    return None
def apply_breach(db,case,a,event,judgment,remedies=None):
    if remedies is None:remedies=db.scalars(select(Remedy).where(Remedy.agreement_id==a.id)).all()
    db.add(Breach(agreement_id=a.id,event_id=event.id,judgment_id=judgment.id))
    a.state='breached';case.status='remedy';a.observation_from=None;a.observation_until=None
    for r in remedies:r.state='reopened'
def decide_equipment(db,case,a,event,judgment):
    # 设备协议按分离出的设备能量判超标；没有覆盖该事件的已完成分离则待主管复核。
    if event.total<=a.threshold:
        judgment.verdict='clear';judgment.decided_at=now(db)
        judgment.basis=f'总能量{event.total:.2f}未超阈值{a.threshold:g}，设备能量占比不超过100%，必然不超标'
        return
    sep=covering_separation(db,case.id,event.id)
    weights=event_weights(sep,event.id) if sep else None
    if not weights or 'equipment' not in weights:
        judgment.verdict='pending';judgment.equipment_energy=None
        judgment.basis='设备能量暂不可分离：尚无覆盖该事件的已完成成分分离，待主管复核是否复发'
        return
    share=weights['equipment'];energy=event.total*share
    judgment.equipment_energy=energy;judgment.decided_at=now(db)
    judgment.verdict='breach' if energy>a.threshold else 'clear'
    judgment.basis=f'按分离设备能量判定：总能量{event.total:.2f}×设备占比{share*100:.1f}%=设备能量{energy:.2f}，阈值{a.threshold:g}（分离记录QS-{sep.id}）'
def judge_event(db,case,event):
    agreements=db.scalars(select(Agreement).where(Agreement.complaint_id==case.id,Agreement.state.in_(SCOPED_STATES))).all()
    for a in agreements:
        remedies=db.scalars(select(Remedy).where(Remedy.agreement_id==a.id)).all()
        completions=[r.completed_at for r in remedies if r.completed_at]
        # 复发必须在整改交付之后且处于约定的静音时段；晚到事件按采集时刻判定。
        if not(completions and event.measured_at>max(completions) and within_window(event.measured_at,a)):continue
        if db.scalar(select(Judgment).where(Judgment.agreement_id==a.id,Judgment.event_id==event.id)):continue
        # 判定依据写全之前不允许 autoflush 落库。
        with db.no_autoflush:
            j=Judgment(agreement_id=a.id,event_id=event.id,mode=a.energy_basis,total_energy=event.total,threshold=a.threshold,created_at=now(db))
            db.add(j)
            if a.energy_basis=='equipment':
                decide_equipment(db,case,a,event,j)
            else:
                j.verdict='breach' if event.total>a.threshold else 'clear';j.decided_at=now(db)
                j.basis=f'原协议按总能量判定：总能量{event.total:.2f}，阈值{a.threshold:g}，{"超出，判定复发" if j.verdict=="breach" else "未超出"}'
        db.flush()
        if j.verdict=='breach':apply_breach(db,case,a,event,j,remedies)
def rejudge_pending(db,case):
    # 成分分离完成后，此前分不清的设备能量事件按新分离结果补判。
    pendings=db.scalars(select(Judgment).join(Agreement,Judgment.agreement_id==Agreement.id).where(Agreement.complaint_id==case.id,Judgment.verdict=='pending')).all()
    for j in pendings:
        a=db.get(Agreement,j.agreement_id)
        if a.state not in SCOPED_STATES:continue
        event=db.get(NoiseEvent,j.event_id)
        remedies=db.scalars(select(Remedy).where(Remedy.agreement_id==a.id)).all()
        completions=[r.completed_at for r in remedies if r.completed_at]
        if not(completions and event.measured_at>max(completions) and within_window(event.measured_at,a)):continue
        decide_equipment(db,case,a,event,j)
        db.flush()
        if j.verdict=='breach':apply_breach(db,case,a,event,j,remedies)
def decide(db,user,id,data):
    j=get(db,Judgment,id);case,a=lock_agreement(db,j.agreement_id);db.refresh(j)
    require(j.verdict=='pending','该事件已完成复发判定')
    require(a.state in SCOPED_STATES,'协议当前状态不可复核判定')
    j.verdict=data.verdict;j.reviewer=user.username;j.decided_at=now(db)
    j.basis=f'{j.basis}；主管{user.username}复核：{data.note}'
    if data.verdict=='breach':apply_breach(db,case,a,get(db,NoiseEvent,j.event_id),j)
    record(db,user,'judgment.decide',j.id,f'{data.verdict};event={j.event_id};{data.note}');db.commit();return dump(j)
