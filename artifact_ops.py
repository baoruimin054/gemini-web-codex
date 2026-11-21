"""File checks and reversible edits. Run only as a normal Codex sandbox tool."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def local_path(value):
    path = Path(value)
    if not path.is_absolute() or not path.drive or str(path).startswith(('\\\\', '//')) or ':' in str(path)[2:]:
        raise ValueError('需要本机绝对文件路径')
    if path.is_symlink() or path.is_junction():
        raise ValueError('链接文件不支持自动快照')
    return path


def inspect(path):
    size = path.stat().st_size
    if not path.is_file():
        raise ValueError('目标不是文件')
    info = dict(path=str(path), bytes=size, sha256=digest(path))
    ext = path.suffix.lower()
    if ext == '.docx':
        with zipfile.ZipFile(path) as package:
            if package.testzip():
                raise ValueError('Word 文档损坏')
            doc = ET.fromstring(package.read('word/document.xml'))
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            info['characters'] = sum(len(e.text or '') for e in doc.iter(ns+'t'))
    elif ext == '.pdf':
        import fitz
        with fitz.open(path) as doc:
            if not len(doc): raise ValueError('PDF 没有页面')
            for page in doc: page.get_displaylist()
            info['pages'] = len(doc)
    elif ext in ('.mp4','.mov','.webm','.mkv'):
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        result = subprocess.run([ffmpeg,'-nostdin','-xerror','-hide_banner','-i',str(path),
                                 '-map','0:v:0','-map','0:a?','-f','null','-'],capture_output=True)
        if result.returncode: raise ValueError('视频解码失败')
        metadata = result.stderr.decode(errors='replace').split('Stream mapping:')[0]
        info.update(has_audio='Audio:' in metadata, duration=re.search(r'Duration: ([\d:.]+)',metadata)[1])
    return info


def checkpoint(mode, token, path):
    if not re.fullmatch('[a-f0-9]{24}',token): raise ValueError('invalid checkpoint')
    folder=Path(tempfile.gettempdir())/'CodexGeminiUndo'/token
    key=hashlib.sha256(str(path).casefold().encode()).hexdigest()
    meta=folder/(key+'.json')
    backup=folder/(key+'.before')
    if mode=='before':
        folder.mkdir(parents=True,exist_ok=True)
        if meta.exists(): raise ValueError('快照已存在')
        previous=digest(path) if path.exists() else None
        if previous is not None:
            shutil.copy2(path,backup)
            if digest(backup)!=previous: raise ValueError('备份期间文件发生变化')
        meta.write_text(json.dumps(dict(path=str(path),before=previous)),encoding='utf8')
    else:
        record=json.loads(meta.read_text(encoding='utf8'))
        if record['path']!=str(path): raise ValueError('快照路径不符')
        current=digest(path) if path.exists() else None
        if mode=='seal':
            if 'after' in record and record['after']!=current: raise ValueError('文件在登记后发生变化')
            record['after']=current
            temp=meta.with_suffix('.new')
            temp.write_text(json.dumps(record),encoding='utf8')
            os.replace(temp,meta)
        elif mode in ('undo','preflight'):
            if 'after' not in record or current!=record['after']: raise ValueError('文件状态已改变，停止撤销')
            if record['before'] is not None and digest(backup)!=record['before']: raise ValueError('备份校验失败')
            if mode=='preflight': return
            if record['before'] is None:
                if current is not None: os.replace(path,folder/(key+'.undone'))
            else:
                with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as out:
                    temp=Path(out.name)
                try:
                    shutil.copy2(backup,temp)
                    os.replace(temp,path)
                finally:
                    temp.unlink(missing_ok=True)
    return dict(path=str(path),checkpoint=token,operation=mode)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('mode',choices=['verify','before','seal','undo'])
    p.add_argument('token');p.add_argument('paths',nargs='+')
    a=p.parse_args()
    results=[]
    if a.mode=='undo':
        for value in a.paths: checkpoint('preflight',a.token,local_path(value))
    for value in a.paths:
        path=local_path(value)
        results.append(inspect(path) if a.mode=='verify' else checkpoint(a.mode,a.token,path))
    print('GEMINI_RECEIPT '+json.dumps(dict(token=a.token,files=results),ensure_ascii=True))
