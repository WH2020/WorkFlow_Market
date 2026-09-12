"""User-confirmed macOS source + .app updates with native crash recovery."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading

from . import macos_update_engine as engine
from . import macos_update_signatures as signatures
from .windows_updates import WindowsUpdateManager, download_asset, MESSAGES as WINDOWS_MESSAGES

MESSAGES = {**WINDOWS_MESSAGES,
    'MACOS_ONLY': '一键更新仅支持已登记的 macOS 桌面安装。',
    'DEPENDENCIES_CHANGED': '此更新与当前运行依赖不兼容或依赖已改变，请按发布说明手动安装；现有数据未改动。',
    'BUNDLE_MODIFIED': '应用包与安装清单不一致，已停止更新；不会覆盖已有改动。',
    'BUNDLE_SIGNATURE_INVALID': '应用包未通过 macOS 代码签名完整性检查，已停止更新。',
    'MISSING_FILE': '此安装缺少 macOS 更新基线，请先手动安装支持一键更新的完整版本。',
    'UNSAFE_OWNER_OR_MODE': '安装路径的所有者或权限不满足更新要求，已停止；不会自动修改已有权限。',
    'UNSAFE_ACL': '安装路径存在不安全或无法确认的 macOS 扩展权限，已停止更新。',
    'RECOVERY_REQUIRED': '更新交接未完成，业务操作保持暂停。请关闭工作台后重新打开以完成恢复；不要删除更新目录。',
    'NATIVE_CONFIRMATION_REQUIRED': '请在 macOS 系统确认框中批准更新；取消或超时不会修改程序。'}


def public_failure(error):
    code = str(error) if isinstance(error, engine.UpdateFailure) else 'UPDATE_FAILED'
    return {'code': code if code in MESSAGES else 'UPDATE_FAILED', 'message': MESSAGES.get(code, MESSAGES['UPDATE_FAILED'])}


class MacOSUpdateManager(WindowsUpdateManager):
    """Reuse only the operation lock, progress, cancellation and commit gate."""
    def _preparation_failed(self, error):
        with self.lock:
            # Either durable pointer means native recovery owns the job. A
            # second-pointer write/fsync failure MUST NOT release business I/O.
            recovery = any(parent is not None and os.path.lexists(parent.parent / 'active')
                           for parent in (self.job, getattr(self, 'support_job', None)))
            self.frozen = recovery
            if recovery:
                error = engine.UpdateFailure('RECOVERY_REQUIRED')
            self.state.update(phase='cancelled' if self.cancel.is_set() and not recovery else 'error',
                              error=public_failure(error))
            self.revision += 1

    def capability(self):
        try:
            engine.require(sys.platform == 'darwin', 'MACOS_ONLY')
            engine.require(os.environ.get('AGENT4MARKET_UPDATE_CONTROL') and os.environ.get('AGENT4MARKET_DESKTOP_PID')
                           and os.environ.get('AGENT4MARKET_UPDATE_PROBE') != '1', 'INSTALLED_DESKTOP_REQUIRED')
            install = engine.installation(self.root)
            signatures.public_key(self.root)
            engine.require(engine.digest(engine.safe_path(self.root, signatures.TRUST_FILE))
                           == install['rows'][signatures.TRUST_FILE]['sha256'], 'PROGRAM_MODIFIED')
            return install, None
        except Exception as error:
            return None, public_failure(error)

    def snapshot(self):
        with self.lock:
            self._refresh_pending()
            install, error = self.capability()
            latest = self.checker.snapshot()
            previous = None
            if install:
                try:
                    result = engine.read_json(self.root / '.pi/app-updates/last-result.json', 4096)
                    if result.get('status') in {'complete', 'rolled_back'}:
                        previous = {key: result.get(key) for key in ('status', 'from_version', 'to_version')}
                except (OSError, ValueError):
                    pass
            return {**copy.deepcopy(self.state), 'revision': self.revision, 'platform': 'macos', 'previous_result': previous,
                    'supported': install is not None, 'unavailable_reason': error['message'] if error else None,
                    'can_start': bool(install and not self.frozen and latest['update_available'] and latest['latest'].get('macos_assets')),
                    'can_cancel': self.frozen and self.state['phase'] in {'downloading', 'staging', 'verifying'},
                    'preserves_data': True, 'confirmation_required': True}

    def start(self, tag, confirmed):
        with self.lock:
            engine.require(confirmed is True, 'CONFIRMATION_REQUIRED')
            if self.frozen:
                return self.snapshot()
            install, error = self.capability()
            engine.require(install, error['code'] if error else 'INSTALLED_DESKTOP_REQUIRED')
            latest = self.checker.snapshot()
            engine.require(latest['update_available'] and latest['latest']['tag'] == tag and latest['latest'].get('macos_assets'), 'STALE_SELECTION')
            engine.require(self.operations == 0 and self.idle(), 'BUSY')
            self.frozen = True
            self.cancel.clear()
            identifier = secrets.token_hex(16)
            self.state = {'phase': 'confirming', 'received_bytes': 0, 'total_bytes': 0, 'error': None, 'job_id': identifier}
            self.revision += 1
            try:
                # Stable-root gate for the existing Pi extension; independent
                # native helper and recovery journal live OUTSIDE both targets.
                parent = engine.mkdirs(self.root, '.pi/app-updates')
                self.job = engine.trusted_directory(parent / identifier, create=True, private=True)
                support_parent = engine.mkdirs(install['support'], 'app-updates')
                self.support_job = engine.trusted_directory(support_parent / identifier, create=True, private=True)
                self.worker = threading.Thread(target=self._prepare, args=(install, copy.deepcopy(latest['latest'])),
                                               name='macos-update-prepare', daemon=True)
                self.worker.start()
            except Exception:
                self.frozen = False
                self.state['phase'] = 'error'
                raise
            return self.snapshot()

    def _prepare(self, install, selected):
        try:
            # macOS TCP has no verified same-UID peer gate here. Therefore a
            # browser token ALONE never authorizes an install: require a real
            # system modal owned by this native application's user as well.
            engine.require(engine.VERSION.fullmatch(selected['version']), 'INVALID_VERSION')
            prompt = ('display dialog "Agent4Market 将升级到 ' + selected['version']
                      + '，同步更新应用与程序，保留现有配置和数据。是否继续？" '
                      'buttons {"取消", "更新"} default button "取消" cancel button "取消" '
                      'with title "Agent4Market 程序更新" giving up after 120')
            approval = subprocess.run(['/usr/bin/osascript', '-e', prompt], stdin=subprocess.DEVNULL,
                                      capture_output=True, timeout=130)
            engine.require(approval.returncode == 0 and 'button returned:更新' in approval.stdout.decode('utf-8')
                           and 'gave up:true' not in approval.stdout.decode('utf-8'), 'NATIVE_CONFIRMATION_REQUIRED')
            self._set(phase='verifying')
            fresh, _etag = self.fetcher(None)
            engine.require(fresh and fresh['tag'] == selected['tag'] and fresh.get('macos_assets') == selected['macos_assets'], 'STALE_SELECTION')
            old = install['manifest']
            engine.verify_programs(self.root, install['rows'])
            engine.verify_bundle(install['app'], old['app_files'], old['version'])
            engine.require(engine.dependency_contract(self.root) == old['dependency_contract']
                           and engine.runtime_fingerprint(self.root) == install['runtime']['fingerprint'], 'DEPENDENCIES_CHANGED')
            rows = selected['macos_assets']
            self._set(phase='downloading')
            self.downloader(rows['signature'], self.support_job / 'signature.json', self.cancel, lambda *_: None)
            signatures.verify(self.root, self.support_job / 'signature.json', selected['version'], rows)
            self.downloader(rows['manifest'], self.support_job / 'new-manifest.json', self.cancel, lambda *_: None)
            new = engine.read_json(self.support_job / 'new-manifest.json')
            new_rows = engine.manifest(new, selected['version'])
            engine.require(tuple(map(int, new['version'].split('.'))) > tuple(map(int, old['version'].split('.'))), 'NOT_AN_UPGRADE')
            engine.require(new['dependency_contract'] == old['dependency_contract']
                           and new_rows[engine.PROTOCOL_FILE] == install['rows'][engine.PROTOCOL_FILE], 'DEPENDENCIES_CHANGED')
            engine.update_space(install, old, new, rows)
            for key in ('programs', 'app'):
                self.downloader(rows[key], self.support_job / (key + '.zip'), self.cancel,
                                lambda received, total: self._set(received_bytes=received, total_bytes=total))
            engine.require(not self.cancel.is_set(), 'CANCELLED')
            self._set(phase='staging')
            stage = self.job / 'stage'
            staged_app = install['app'].parent / ('.Agent4Market-update-' + self.job.name + '.app')
            engine.require(not os.path.lexists(stage) and not os.path.lexists(staged_app), 'STAGE_EXISTS')
            engine.extract_archive(self.support_job / 'programs.zip', stage, new['files'])
            engine.extract_archive(self.support_job / 'app.zip', staged_app, new['app_files'], app=True)
            engine.verify_bundle(staged_app, new['app_files'], new['version'])
            engine.require(engine.dependency_contract(stage) == new['dependency_contract'], 'DEPENDENCIES_CHANGED')
            engine.require(not self.cancel.is_set(), 'CANCELLED')
            self._set(phase='verifying')
            helper_source = install['app'] / 'Contents/MacOS/Agent4Market'
            helper = self.support_job / 'native-helper'
            engine.write_atomic(helper, helper_source.read_bytes(), mode=0o700)
            engine.require(engine.digest(helper) == next(row['sha256'] for row in old['app_files'] if row['path'] == 'Contents/MacOS/Agent4Market'), 'BUNDLE_MODIFIED')
            checked = subprocess.run([str(helper), '--macos-update-helper-self-test'], cwd=install['support'],
                env={'PATH': '/usr/bin:/bin', 'HOME': str(Path.home())}, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
            engine.require(checked.returncode == 0 and checked.stdout.strip() == b'Agent4Market macOS native recovery v1', 'UPDATE_PROTOCOL_UNSUPPORTED')
            engine.write_atomic(self.support_job / 'old-manifest.json', (self.root / engine.MANIFEST_FILE).read_bytes())
            plan = {'format': 1, 'platform': 'macos-universal', 'root': str(self.root), 'job_id': self.job.name,
                    'from_version': install['version'], 'to_version': selected['version'], 'nonce': secrets.token_hex(32),
                    'old_manifest_sha256': engine.digest(self.root / engine.MANIFEST_FILE),
                    'new_manifest_sha256': rows['manifest']['sha256'], 'helper_sha256': engine.digest(helper)}
            engine.write_json(self.support_job / 'job.json', plan)
            engine.write_json(self.job / 'job.json', plan)  # Only the Pi trial receipt/gate, not recovery code.
            with self.lock:
                engine.require(self.operations == 0 and self.idle(), 'BUSY')
                engine.require(not self.cancel.is_set(), 'CANCELLED')
                engine.update_space(install, old, new, rows, staged=True)
                pointer = (self.job.name + '\n' + selected['version'] + '\n').encode()
                engine.write_atomic(self.support_job.parent / 'active', pointer)
                engine.write_atomic(self.job.parent / 'active', pointer)
                self.state.update(phase='ready', target_version=selected['version'])
                self.revision += 1
        except Exception as error:
            self._preparation_failed(error)

    def native_abort(self):
        with self.lock:
            if self.state['phase'] == 'ready':
                try:
                    for parent in (self.job, self.support_job):
                        pointer = engine.safe_path(parent.parent, 'active')
                        engine.require(pointer.read_text().splitlines() == [self.job.name, self.state['target_version']], 'INVALID_JOB')
                        os.replace(pointer, parent / 'aborted-active')
                        engine.sync_directory(parent.parent)
                        engine.sync_directory(parent)
                finally:
                    self._preparation_failed(engine.UpdateFailure('NATIVE_STOP_FAILED'))
