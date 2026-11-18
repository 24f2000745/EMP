"""Celery batch jobs for CSV export, daily reminders and monthly reporting."""
import os, csv, sqlite3, smtplib, html
from pathlib import Path
from email.message import EmailMessage
from datetime import date, datetime, timedelta
from celery_app import celery

BASE=Path(__file__).resolve().parent
DB=Path(os.environ.get('EMP_DATABASE',BASE/'emp.db'))
EXPORT_DIR=Path(os.environ.get('EMP_EXPORT_DIR',BASE/'instance'/'exports'))

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c

def send_email(to,subject,body,html_body=None):
    host=os.environ.get('SMTP_HOST'); user=os.environ.get('SMTP_USER'); password=os.environ.get('SMTP_PASSWORD')
    if not host or not to: return False
    msg=EmailMessage(); msg['Subject']=subject; msg['From']=os.environ.get('SMTP_FROM',user or 'emp@localhost'); msg['To']=to; msg.set_content(body)
    if html_body: msg.add_alternative(html_body,subtype='html')
    with smtplib.SMTP(host,int(os.environ.get('SMTP_PORT','587')),timeout=20) as s:
        s.starttls()
        if user: s.login(user,password or '')
        s.send_message(msg)
    return True

@celery.task(name='tasks.export_student_history')
def export_student_history(student_id):
    EXPORT_DIR.mkdir(parents=True,exist_ok=True)
    c=db(); row=c.execute('INSERT INTO export_jobs(student_id,status) VALUES(? ,\'Running\')',(student_id,)); job_id=row.lastrowid; c.commit()
    try:
        rows=c.execute('''SELECT u.id student_id,e.name examination_name,co.name course,s.exam_date examination_date,ex.full_name examiner,ev.total_marks marks_obtained,b.status booking_status FROM bookings b JOIN users u ON u.id=b.student_id JOIN exam_slots s ON s.id=b.slot_id JOIN examinations e ON e.id=s.examination_id JOIN courses co ON co.id=e.course_id JOIN users ex ON ex.id=s.examiner_id LEFT JOIN evaluations ev ON ev.booking_id=b.id WHERE b.student_id=? ORDER BY s.exam_date DESC''',(student_id,)).fetchall()
        path=EXPORT_DIR/f'student-{student_id}-history-{job_id}.csv'
        with path.open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.writer(f); w.writerow(['Student ID','Examination Name','Course','Examination Date','Examiner','Marks Obtained','Booking Status'])
            for r in rows: w.writerow([r['student_id'],r['examination_name'],r['course'],r['examination_date'],r['examiner'],r['marks_obtained'],r['booking_status']])
        c.execute("UPDATE export_jobs SET status='Completed',file_path=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",(str(path),job_id)); c.execute('INSERT INTO notifications(user_id,title,message) VALUES(?,?,?)',(student_id,'Export ready',f'Your examination history CSV (job #{job_id}) is ready to download.')); c.commit()
        return {'job_id':job_id,'status':'Completed'}
    except Exception:
        c.execute("UPDATE export_jobs SET status='Failed' WHERE id=?",(job_id,)); c.commit(); raise
    finally: c.close()

@celery.task(name='tasks.send_daily_reminders')
def send_daily_reminders():
    c=db(); today=date.today().isoformat(); tomorrow=(date.today()+timedelta(days=1)).isoformat()
    rows=c.execute('''SELECT u.email,u.id student_id,e.name exam_name,s.exam_date,s.start_time,s.end_time,ex.full_name examiner,e.instructions FROM bookings b JOIN users u ON u.id=b.student_id JOIN exam_slots s ON s.id=b.slot_id JOIN examinations e ON e.id=s.examination_id JOIN users ex ON ex.id=s.examiner_id WHERE b.status='Booked' AND s.exam_date BETWEEN ? AND ?''',(today,tomorrow)).fetchall()
    sent=0
    for r in rows:
        body=f"Reminder: {r['exam_name']} on {r['exam_date']} from {r['start_time']} to {r['end_time']}. Examiner: {r['examiner']}. Instructions: {r['instructions'] or 'Check your portal.'}"
        c.execute('INSERT INTO notifications(user_id,title,message) VALUES(?,?,?)',(r['student_id'],'Examination reminder',body))
        try: send_email(r['email'],'Examination reminder',body)
        except Exception: pass
        sent+=1
    c.commit(); c.close(); return {'reminders':sent}

@celery.task(name='tasks.send_monthly_report')
def send_monthly_report():
    today=date.today()
    if today.day != 1: return {'skipped':'Runs on first day of month only'}
    first=today.replace(day=1); prev=first-timedelta(days=1); start=prev.replace(day=1).isoformat(); end=first.isoformat()
    c=db(); stats={
      'Examinations conducted':c.execute("SELECT COUNT(DISTINCT s.examination_id) FROM evaluations ev JOIN bookings b ON b.id=ev.booking_id JOIN exam_slots s ON s.id=b.slot_id WHERE ev.status='Submitted' AND date(ev.evaluated_at) BETWEEN ? AND ?",(start,end)).fetchone()[0],
      'Students evaluated':c.execute("SELECT COUNT(*) FROM evaluations WHERE status='Submitted' AND date(evaluated_at) BETWEEN ? AND ?",(start,end)).fetchone()[0],
      'Completed bookings':c.execute("SELECT COUNT(*) FROM bookings WHERE status='Completed' AND date(completed_at) BETWEEN ? AND ?",(start,end)).fetchone()[0],
      'Pending evaluations':c.execute("SELECT COUNT(*) FROM evaluations WHERE status='Pending'",()).fetchone()[0],
    }
    active=c.execute("SELECT u.full_name,COUNT(ev.id) n FROM users u JOIN evaluations ev ON ev.examiner_id=u.id WHERE ev.status='Submitted' AND date(ev.evaluated_at) BETWEEN ? AND ? GROUP BY u.id ORDER BY n DESC LIMIT 5",(start,end)).fetchall()
    admin=c.execute("SELECT email FROM users WHERE role='admin' LIMIT 1").fetchone(); c.close()
    table=''.join(f'<tr><td>{html.escape(k)}</td><td>{v}</td></tr>' for k,v in stats.items())
    people=''.join(f'<li>{html.escape(r["full_name"])} — {r["n"]} evaluations</li>' for r in active) or '<li>No evaluations recorded</li>'
    html_body=f'<h2>EMP Monthly Report</h2><p>Period: {start} to {end}</p><table border="1" cellpadding="8">{table}</table><h3>Most active examiners</h3><ul>{people}</ul>'
    if admin:
        try: send_email(admin['email'],'EMP Monthly Examination Report',f'Monthly report {start} to {end}: '+str(stats),html_body)
        except Exception: pass
    return {'period':f'{start} to {end}','stats':stats}
