"""Independent, versioned train-code candidates. No network, routing or date activation.

Spans are Python character offsets into the original text, including after
full-width ASCII normalization. Candidate syntax never establishes a real train,
an old/new identity mapping, sale eligibility or provider support.
"""
from dataclasses import dataclass
from enum import Enum
import re


class Version(str, Enum):
    LEGACY = 'legacy'
    REFORM_2026 = 'reform_2026'


class Status(str, Enum):
    CANDIDATE = 'candidate'
    UNKNOWN_PREFIX = 'unknown_prefix'
    AMBIGUOUS = 'ambiguous'
    REJECTED = 'rejected'


class Domain(str, Enum):
    PASSENGER = 'passenger_candidate'
    FREIGHT = 'freight_candidate'
    WORK = 'work_candidate'
    UNKNOWN = 'unknown'


_LEGACY = frozenset('GDCTZKYSLBN')
_REFORM = frozenset({'X','G','T','K','D','C','S','Z','N','V','Y','B','DH','DJ'})
_LEX = re.compile(r'[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*')
_CODE = re.compile(r'(?P<zero>0?)(?P<prefix>[A-Z]+)(?P<number>\d+)(?P<suffix>[A-Z]?)$', re.I)
_MODEL = re.compile(r'(?<![A-Za-z0-9])(?:CRH?\d[A-Z0-9]*|HXD\d[A-Z0-9]*|DF\d[A-Z0-9]*|SS\d[A-Z0-9]*)'
                    r'(?:-[A-Z0-9]+)*(?:/\d{3,5}[A-Z]?)*(?![A-Za-z0-9])', re.I)
_DATES = re.compile(r'(?<![A-Za-z0-9])(?:\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?'
                    r'|\d{4}年(?:\d{1,2}月(?:\d{1,2}[日号]?)?)?'
                    r'|\d{1,2}月\d{1,2}[日号]|\d{1,2}:\d{2})(?![A-Za-z0-9])')
_UNIT = re.compile(r'\s*(?:公里|千米|km|元|张|辆|节|组|年|月|日|号|点|分|秒|时(?!刻))', re.I)
_NUMERIC_LIST = re.compile(r'\s*\d{1,5}(?:\s*(?:、|,|，|/|和|及|与)\s*\d{1,5})*\s*')


@dataclass(frozen=True)
class Token:
    raw: str
    normalized: str
    start: int
    end: int
    prefix: str
    number: str
    suffix: str
    version: Version
    status: Status
    domain: Domain
    reason: str
    leading_zero: bool = False


@dataclass(frozen=True)
class ParseResult:
    version: Version
    tokens: tuple[Token, ...]

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(t.normalized for t in self.tokens if t.status is Status.CANDIDATE))

    @property
    def unknown(self) -> tuple[Token, ...]:
        return tuple(t for t in self.tokens if t.status is Status.UNKNOWN_PREFIX)


def _ascii(text: str) -> str:
    # Deliberately avoid NFKC expansion (e.g. one circled character -> two digits).
    return ''.join(chr(ord(c)-0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E
                   else ' ' if c == '\u3000' else c for c in text)


def _domain(prefix: str, number: str, version: Version, zero: bool) -> Domain:
    if zero:
        return Domain.UNKNOWN
    if version is Version.LEGACY:
        return Domain.PASSENGER if (not prefix or prefix in _LEGACY-{'B'}) and len(number)<=4 else Domain.UNKNOWN
    if prefix and prefix not in _REFORM:
        return Domain.UNKNOWN
    if prefix in {'B','DH'}:
        return Domain.FREIGHT
    if prefix == 'DJ':
        return Domain.WORK
    if not prefix:
        value=int(number)
        return Domain.FREIGHT if (71001<=value<=77998 or 80001<=value<=84998
                                  or 85001<=value<=85998) else Domain.UNKNOWN
    # Five-digit prefixed trial/work allocations remain insufficiently confirmed.
    return Domain.PASSENGER if len(number)<=4 else Domain.UNKNOWN


def parse(text: str, *, version: Version | str = Version.REFORM_2026,
          exclude_spans: tuple[tuple[int,int], ...] = ()) -> ParseResult:
    """Parse complete lexical units; invalid/unknown units cannot yield tail digits.

    Bare numeric tokens need standalone/list or explicit train context. Slash
    suffix abbreviations are represented as ambiguous tokens, without expansion.
    The version is explicitly selected by the caller, never inferred from today.
    """
    version=Version(version)
    clean=_ascii(text)
    exclusions=[(m.start(),m.end(),'rolling_stock') for m in _MODEL.finditer(clean)]
    exclusions += [(m.start(),m.end(),'date_or_clock') for m in _DATES.finditer(clean)]
    for start,end in exclude_spans:
        if not (0<=start<end<=len(text)):
            raise ValueError('exclude_spans must be valid original character offsets')
        exclusions.append((start,end,'excluded_span'))
    # A reform-period parser must still preserve historical identifiers.
    known=_LEGACY if version is Version.LEGACY else _REFORM | _LEGACY
    numeric_list=bool(_NUMERIC_LIST.fullmatch(clean))
    tokens=[]
    previous=None
    for match in _LEX.finditer(clean):
        start,end=match.span()
        normalized=match.group().upper()
        prefix=number=suffix=''
        zero=False
        status=Status.REJECTED
        domain=Domain.UNKNOWN
        reason=next((why for a,b,why in exclusions if start<b and a<end),'')
        before,after=clean[:start],clean[end:]
        code=_CODE.fullmatch(normalized)
        if reason:
            pass
        elif code:
            prefix,number,suffix=code.group('prefix','number','suffix')
            zero=bool(code.group('zero'))
            if len(number)>5 or not int(number):
                reason='invalid_number_width_or_zero'
            elif prefix not in known:
                status,reason=Status.UNKNOWN_PREFIX,'unknown_prefix'
            else:
                status=Status.CANDIDATE
                reason=('legacy_only' if version is Version.REFORM_2026 and prefix not in _REFORM else 'syntax_only')
                domain=_domain(prefix,number,version,zero)
        elif normalized.isdecimal():
            number=normalized
            if (previous is not None and clean[previous.end:start].strip()=='/'
                    and (previous.prefix or previous.reason=='linked_numeric_suffix')):
                status,reason=Status.AMBIGUOUS,'linked_numeric_suffix'
            elif normalized in {'12306','95306'}:
                reason='service_number'
            elif len(number)>5 or not int(number):
                reason='invalid_number_width_or_zero'
            elif re.fullmatch(r'(?:19|20)\d{2}',number):
                status,reason=Status.AMBIGUOUS,'year_shaped'
            elif _UNIT.match(after) or (re.search(r'编号\s*$',before) and '车次' not in before[-8:]):
                reason='numeric_non_train_context'
            elif len(number)==5 and version is Version.LEGACY:
                status,reason=Status.AMBIGUOUS,'unsupported_legacy_width'
            elif (numeric_list or re.match(r'\s*次',after)
                  or re.search(r'(?:车次|列车)\s*$',before)):
                status,reason=Status.CANDIDATE,'numeric_syntax_only'
                domain=_domain('',number,version,False)
            else:
                status,reason=Status.AMBIGUOUS,'bare_number_requires_context'
        else:
            reason='opaque_invalid_token'
        token=Token(text[start:end],normalized,start,end,prefix,number,suffix,
                    version,status,domain,reason,zero)
        tokens.append(token)
        previous=token
    return ParseResult(version,tuple(tokens))
