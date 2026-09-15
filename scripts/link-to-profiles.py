"""确保每个 Hermes profile 通过目录联接（junction）使用 default 的 anysearch 插件源。

为什么需要它
------------
Hermes 的用户插件目录是 per-profile 的（``get_hermes_home()/plugins``），所以插件要在
N 个 profile 可用就有 N 份副本 —— 复制方案必然漂移。目录联接让所有 profile 指向
同一份实体：改一处，所有 profile 立即生效（Hermes 的插件扫描器能正常识别 junction，
已在 6.0 版本实测验证）。

Windows 用 junction（mklink /J，不需要管理员权限）；类 Unix 用 symlink。

它取代了 scripts/sync-to-profiles.sh（复制式同步）—— 只有当 junction 因故不可用时
才退回复制方案。

用法
----
    python link-to-profiles.py            # 为所有 profile 建立/修复联接
    python link-to-profiles.py --verify   # 只检查现状，不做修改

新 profile 加入后重跑一次即可。注意：``plugins.enabled`` 与 ``.env`` 仍是 per-profile 的，
新建 profile 仍需 ``hermes -p <p> plugins enable anysearch`` 与 key 写入。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERMES_HOME = os.environ.get('HERMES_HOME') or os.path.join(os.path.expanduser('~'), '.hermes')
SRC = os.path.join(HERMES_HOME, 'plugins', 'anysearch')
LINK_NAME = 'anysearch'
IS_WINDOWS = os.name == 'nt'


def current_realpath(path: str) -> str:
    return os.path.realpath(path).rstrip('\\/').lower()


def verify_only() -> int:
    print('源目录:', os.path.realpath(SRC))
    bad = 0
    for name in sorted(os.listdir(os.path.join(HERMES_HOME, 'profiles'))):
        link = os.path.join(HERMES_HOME, 'profiles', name, 'plugins', LINK_NAME)
        if not os.path.isdir(link):
            print('  %-18s 缺失' % name)
            bad += 1
            continue
        same = current_realpath(link) == current_realpath(SRC)
        print('  %-18s %s' % (name, '指向源 ✓' if same else '独立副本！(%s)' % os.path.realpath(link)))
        if not same:
            bad += 1
    return bad


def make_link(link: str) -> bool:
    if IS_WINDOWS:
        r = subprocess.run(['cmd', '/c', 'mklink', '/J', link, SRC], capture_output=True)
        return r.returncode == 0
    os.symlink(SRC, link, target_is_directory=True)
    return True


def relink() -> int:
    if not os.path.isdir(SRC):
        print('源目录不存在:', SRC)
        return 1
    changed = 0
    for name in sorted(os.listdir(os.path.join(HERMES_HOME, 'profiles'))):
        prof_dir = os.path.join(HERMES_HOME, 'profiles', name)
        if not os.path.isdir(prof_dir):
            continue
        link = os.path.join(prof_dir, 'plugins', LINK_NAME)
        os.makedirs(os.path.dirname(link), exist_ok=True)

        if os.path.isdir(link) or os.path.islink(link):
            if current_realpath(link) == current_realpath(SRC):
                print('  %-18s 已是联接，跳过' % name)
                continue
            # 先尝试删链接本身（junction/symlink），失败再按实体目录删
            try:
                os.rmdir(link)
            except OSError:
                shutil.rmtree(link)
            print('  %-18s 移除原有独立副本' % name)

        ok = make_link(link)
        print('  %-18s %s' % (name, '联接创建成功' if ok else '!! 创建失败'))
        if ok:
            changed += 1
    print('\n完成，%d 个 profile 已（重新）建立联接。' % changed)
    print('提醒: 让用户完全退出并重开各 profile 会话（插件在启动时加载）。')
    return 0


if __name__ == '__main__':
    if '--verify' in sys.argv:
        sys.exit(0 if verify_only() == 0 else 1)
    sys.exit(relink())
