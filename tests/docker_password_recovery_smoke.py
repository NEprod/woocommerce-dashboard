"""Network-isolated two-worker upgrade/restart plus secure local recovery."""
import sys
import docker_refresh_reporting_smoke as previous

smoke = previous.smoke
smoke.IMPORT += '''
from app.models import User
from werkzeug.security import generate_password_hash
with app.app_context():
 db.session.add(User(username='local-recovery-fixture',email='recovery@example.test',
  password=generate_password_hash('fictional-recovery-original'),is_admin=True))
 db.session.commit()
'''
smoke.CHECK += '''
help_page=opener.open('http://127.0.0.1:7485/forgot-password',timeout=5).read().decode()
assert 'Self-service password recovery is currently unavailable' in help_page
assert '<form' not in help_page and 'No reset email will be sent' in help_page
from itsdangerous import URLSafeTimedSerializer
old_token=URLSafeTimedSerializer('orders-offline-smoke-secret-000000000000000000').dumps(
 'fixture@example.test',salt='password-reset-salt')
old_link=opener.open('http://127.0.0.1:7485/reset-password/'+old_token,timeout=5)
assert old_link.geturl()=='http://127.0.0.1:7485/forgot-password'

# Exercise real getpass/termios rather than a piped password or a mocked prompt.
import os,pty,select,time
new_password='fictional-recovery-new-password'
pid,terminal=pty.fork()
if pid==0:
 os.execv(sys.executable,[sys.executable,'-m','app.admin_recovery'])
transcript=b'';sent=0;exit_status=None;deadline=time.monotonic()+20
prompts=[b'Existing administrator username:',b'New password (at least 12 characters):',b'Confirm new password:']
answers=['local-recovery-fixture',new_password,new_password]
try:
 while time.monotonic()<deadline:
  if select.select([terminal],[],[],0.2)[0]:
   try:chunk=os.read(terminal,4096)
   except OSError:break
   if not chunk:break
   transcript+=chunk
  if sent<len(prompts) and prompts[sent] in transcript:
   os.write(terminal,(answers[sent]+'\\n').encode());sent+=1
  waited,status=os.waitpid(pid,os.WNOHANG)
  if waited:
   exit_status=status;break
finally:
 os.close(terminal)
 if exit_status is None:
  waited,status=os.waitpid(pid,os.WNOHANG)
  if not waited:
   os.kill(pid,9);os.waitpid(pid,0)
   raise AssertionError('Local recovery did not complete within its bounded prompt test')
  exit_status=status
assert os.waitstatus_to_exitcode(exit_status)==0 and sent==3
assert new_password.encode() not in transcript
assert b'Administrator password updated' in transcript
from werkzeug.security import check_password_hash
with sqlite3.connect('/app/instance/site.db') as connection:
 retained=connection.execute("select password from user where username='local-recovery-fixture'").fetchone()[0]
 assert check_password_hash(retained,new_password)
 assert connection.execute("select username from user where id=1").fetchone()[0]=='orders-smoke'
'''

if __name__ == '__main__':
    smoke.main(sys.argv[1])
