"""Educational user authentication and sandboxed, role-controlled file access."""
import os, hashlib, hmac, secrets
from pathlib import Path
class Security:
    def __init__(self, root=None):
        self.root=Path(root or (Path.home()/'.myshell_d4_demo')).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.records={}
        for username,password,role in [('admin','AdminDemo!2026','admin'),('student','StudentDemo!2026','standard')]:
            salt=secrets.token_bytes(16)
            self.records[username]=(salt,hashlib.pbkdf2_hmac('sha256',password.encode(),salt,180000),role)
        self.username=None;self.role=None
        (self.root/'system').mkdir(exist_ok=True)
        (self.root/'shared').mkdir(exist_ok=True)
        (self.root/'system'/'policy.txt').write_text('System policy: admin modification only\n') if not (self.root/'system'/'policy.txt').exists() else None
    def login(self,username,password):
        record=self.records.get(username)
        if record is None:
            # Burn nearly the same password-hash effort for unknown users.
            hashlib.pbkdf2_hmac('sha256',password.encode(),b'0000000000000000',180000)
            return False
        salt,expected,role=record
        actual=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,180000)
        if not hmac.compare_digest(expected,actual):return False
        self.username=username;self.role=role
        (self.root/'users'/username).mkdir(parents=True,exist_ok=True)
        return True
    def logout(self): self.username=self.role=None
    def resolve(self,name,mode):
        if self.username is None:raise PermissionError('login required')
        if name.startswith('/') or '..' in Path(name).parts:raise PermissionError('path escape denied')
        parts=Path(name).parts
        if not parts:raise ValueError('empty filename')
        if parts[0]=='system':
            if self.role!='admin' and mode in ('write','execute'):raise PermissionError('standard users cannot modify or execute system files')
        elif parts[0]=='shared':pass
        elif parts[:2]==('users',self.username):pass
        elif self.role=='admin' and parts[0]=='users':pass
        else:raise PermissionError('access denied for this role/path')
        path=(self.root/name).resolve()
        if not path.is_relative_to(self.root):raise PermissionError('symlink escape denied')
        return path
    def read(self,name):return self.resolve(name,'read').read_text()
    def write(self,name,content):
        path=self.resolve(name,'write');path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
        return f'WRITE OK: {name} by {self.username} ({self.role})'
