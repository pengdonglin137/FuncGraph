"""Conservative, symbol-matched user API return conventions, not errno capture."""

import re


MINUS_ONE_APIS = frozenset('''
    open open64 openat openat64 creat creat64 close read write pread pread64 pwrite pwrite64
    lseek lseek64 fsync fdatasync truncate truncate64 ftruncate ftruncate64
    unlink unlinkat mkdir mkdirat rmdir rename renameat renameat2
    stat stat64 lstat lstat64 fstat fstat64 fstatat fstatat64 access faccessat
    chmod fchmod fchmodat chown fchown lchown fchownat chdir fchdir
    socket socketpair bind connect listen accept accept4 shutdown getsockopt setsockopt
    send sendto sendmsg recv recvfrom recvmsg dup dup2 dup3 pipe pipe2
'''.split())
NULL_APIS = frozenset(('fopen', 'fopen64', 'fdopen', 'freopen', 'freopen64', 'opendir', 'fdopendir'))
DIRECT_ERRNO_APIS = frozenset(('pthread_create', 'pthread_join', 'pthread_detach', 'posix_memalign'))
API_SYMBOL = re.compile(r'([A-Za-z_][A-Za-z_0-9]*)(?:@plt|@@?GLIBC_[0-9]+(?:\.[0-9]+)*)?\Z')
SCALAR = re.compile(r'(?:-?[0-9]{1,20}|0x[0-9a-fA-F]{1,16})\Z')


def classify_user_return(name, value, errno_map):
    if not isinstance(name, str):
        return None
    api = name
    if '@' in name:
        symbol = API_SYMBOL.fullmatch(name)
        if symbol is None:
            return None
        api = symbol[1]
    if api not in MINUS_ONE_APIS and api not in NULL_APIS and api not in DIRECT_ERRNO_APIS:
        return None
    if not isinstance(value, str) or not SCALAR.fullmatch(value):
        return None
    number = int(value, 16 if value.startswith('0x') else 10)
    if api in MINUS_ONE_APIS and number == -1:
        rule, summary = 'minus_one', 'API failure · errno not recorded'
        convention = '-1 indicates failure'
    elif api in NULL_APIS and number == 0:
        rule, summary = 'null', 'API failure · NULL'
        convention = 'NULL indicates failure'
    elif api in DIRECT_ERRNO_APIS and 0 < number <= 0x7fffffff:
        label = errno_map.get(-number)
        return {'rule': 'direct_errno', 'errno': number,
                'summary': 'API error · ' + (label + ' (' + str(number) + ')' if label else str(number)),
                'note': 'Symbol-matched ' + api + '() convention: the API returns an error number directly, '
                        'not a captured errno value. Symbol identity and error propagation are not verified.'}
    else:
        return None
    return {'rule': rule, 'summary': summary,
            'note': 'Symbol-matched ' + api + '() convention: ' + convention + '. errno was not recorded; '
                    'no specific errno is inferred. Symbol identity and error propagation are not verified.'}
