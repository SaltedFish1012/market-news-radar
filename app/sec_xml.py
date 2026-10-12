"""Bounded XML reading; no DTD, external entities, or legacy encodings."""
import re
from xml.etree import ElementTree as ET

MAX_RESPONSE_BYTES=20*1024*1024


def parse_xml(data:bytes)->ET.Element:
    if not isinstance(data,bytes) or len(data)>MAX_RESPONSE_BYTES:
        raise ValueError('SEC XML 大小超限')
    try:
        text=data.decode('utf-8-sig')
    except UnicodeError as exc:
        raise ValueError('SEC XML 编码不支持') from exc
    if '\x00' in text or re.search(r'<!\s*(DOCTYPE|ENTITY)\b',text,re.I):
        raise ValueError('SEC XML 不允许 DTD 或实体')
    encoding=re.search(r'<\?xml[^>]*encoding\s*=\s*[\'"]([^\'"]+)',text,re.I)
    if encoding and encoding[1].lower() not in ('utf-8','utf8','us-ascii','ascii'):
        raise ValueError('SEC XML 编码不支持')
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError('SEC XML 格式无效') from exc


def local_name(element)->str:
    return element.tag.rsplit('}',1)[-1].casefold()


def elements(root,name:str)->list:
    return [e for e in root.iter() if local_name(e)==name.casefold()]


def first(root,name:str):
    return next((e for e in root.iter() if local_name(e)==name.casefold()),None)


def text(root,name:str,default='')->str:
    found=first(root,name)
    return ''.join(found.itertext()).strip() if found is not None else default
