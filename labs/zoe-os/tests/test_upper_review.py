"""Synthetic regressions for the upper-stack review; no sockets or live reads."""
import io

import pytest
import real_turn as rt
from spine_helpers import AllowList
from zoe_os.events import Event, Kind
from zoe_os.spine import Spine, Refused


def test_log_reader_never_buffers_or_decodes_a_body(tmp_path, monkeypatch, capsys):
    log = tmp_path / '.zoe-logs' / 'zoe-data.app.log'
    log.parent.mkdir()
    secret = b'credential-synthetic-body-9347'
    log.write_bytes(b'2030-03-04T23:00:00+0000 INFO routers.chat: ' + secret + b'\n')
    monkeypatch.setattr(rt.Path, 'home', staticmethod(lambda: tmp_path))
    real_open = open
    real_age = rt.last_turn_age_s
    def prefix_age(prefixes, now):
        assert secret.decode() not in prefixes
        return real_age(prefixes, now)
    monkeypatch.setattr(rt, 'last_turn_age_s', prefix_age)
    class PrefixOnly(io.BytesIO):
        def read(self, size=-1):
            assert size == 1, 'log must be scanned one byte at a time'
            return super().read(size)
    monkeypatch.setattr(rt, 'open', lambda path, mode, **kw: PrefixOnly(real_open(path, mode).read()), raising=False)
    age = rt._log_age()
    assert isinstance(age, float)
    assert secret.decode() not in str(age) + str(capsys.readouterr())


def test_process_identity_never_reads_credential_arguments(tmp_path, capsys, monkeypatch):
    proc = tmp_path / '123'
    proc.mkdir()
    (proc / 'comm').write_text('python3\n')
    (proc / 'cmdline').write_bytes(b'/usr/bin/python3\0/task/measure_voice.py\0--credential=synthetic-9347\0')
    raw = (proc / 'cmdline').read_bytes()
    boundary = raw.index(b'--credential')
    real_open = open
    class IdentityOnly(io.BytesIO):
        def read(self, size=-1):
            assert size == 1 and self.tell() + size <= boundary, 'read beyond process identity'
            return super().read(size)
    def identity_open(path, mode, **kwargs):
        return IdentityOnly(raw) if path.name == 'cmdline' else real_open(path, mode, **kwargs)
    monkeypatch.setattr(rt, 'open', identity_open, raising=False)
    path_type = rt.Path
    monkeypatch.setattr(rt, 'Path', lambda path: tmp_path if path == '/proc' else path_type(path))
    found = rt._proc_cmdlines()
    assert found == ['python3 measure_voice.py']
    assert 'synthetic-9347' not in str(found) + str(capsys.readouterr())


def test_subscriber_cache_loses_forgotten_and_derived_events(spine):
    sub = spine.subscribe('all', from_seq=0)
    source = spine.publish(Event(Kind.UTTERANCE, 'voice', 'person-a', payload={'text': 'synthetic-secret'}))
    spine.publish(Event(Kind.OFFER, 'skill', 'person-a', caused_by=(source.seq,),
                        payload={'offer_id': 'o1', 'text': 'synthetic-derived'}))
    while sub.poll() is not None:
        pass
    assert 'synthetic-secret' in str([e.payload for e in sub.cache])
    assert 'synthetic-derived' in str([e.payload for e in sub.cache])
    spine.forget('person-a', actor='person-a')
    assert list(sub.cache) == []  # immediate: no pump or cooperative subscriber needed


@pytest.mark.parametrize('url', ['http://127.0.0.1:11434', 'http://127.2.3.4:1/',
                                    'http://[::1]:8080', 'http://localhost:8080'])
def test_loopback_urls_accepted(url):
    assert rt.validate_llama_url(url) == url.rstrip('/')


@pytest.mark.parametrize('url', ['https://127.0.0.1:1', 'http://example.invalid:80',
    'http://127.0.0.1', 'http://user:pass@localhost:80', 'http://127.1:80',
    'http://2130706433:80', 'http://localhost.:80', 'http://%6cocalhost:80',
    'http://localhost:80/path', 'http://localhost:80?x=1', 'http://localhost:80#x',
    'http://localhost:0', 'http://[::ffff:127.0.0.1]:80', ' http://localhost:80',
    'http://localhost:80\\@example.invalid', 'http://local\nhost:80'])
def test_unsafe_urls_refused_before_any_io(url, monkeypatch):
    monkeypatch.setattr(rt, 'take_lock', lambda *_: pytest.fail('I/O before URL validation'))
    with pytest.raises(ValueError, match='loopback'):
        rt.main(['--llama-url', url])


def test_redirects_are_refused():
    with pytest.raises(ValueError, match='redirect'):
        rt.NoRedirect().redirect_request(None, None, 302, '', {}, 'http://example.invalid:80')


def test_paths_resolve_under_allowlisted_roots_only(tmp_path, monkeypatch):
    base = tmp_path / 'state'
    base.mkdir()
    monkeypatch.setattr(rt, 'STATE_DIR', base, raising=False)
    monkeypatch.setattr(rt, 'PRIVATE_TOOLS_DIR', tmp_path / 'private', raising=False)
    assert rt.lab_path(base / 'result') == base / 'result'
    assert rt.lab_path(tmp_path / 'private' / 'result') == tmp_path / 'private' / 'result'
    (base / 'escape').symlink_to(tmp_path, target_is_directory=True)
    for path in (tmp_path / 'outside', base / '..' / 'outside', base / 'escape' / 'result'):
        with pytest.raises(ValueError, match='lab path'):
            rt.lab_path(path)


@pytest.fixture
def spine(tmp_path):
    s = Spine(str(tmp_path / 's.db'), AllowList(origins={'voice'}, skills={'skill'},
              readers={'skill': {'*'}, 'all': {'*'}}))
    yield s
    s.close()


def claim(cause, receipt=None):
    return Event(Kind.CLAIM, 'skill', 'person-a', payload={'action': 'test', 'receipt_seq': receipt},
                 caused_by=(cause,))


@pytest.mark.parametrize('receipt', [None, -1, True, [], 2**100])
def test_unbacked_claim_refused_before_delivery(spine, receipt):
    cause = spine.publish(Event(Kind.UTTERANCE, 'voice', 'person-a', payload={'text': 'synthetic'}))
    sub = spine.subscribe('all', kinds={Kind.CLAIM})
    with pytest.raises(Refused, match='receipt'):
        spine.publish(claim(cause.seq, receipt))
    assert sub.poll() is None
    assert not list(spine.replay('all', kinds={Kind.CLAIM}))


@pytest.mark.parametrize('change', [{'verified': False}, {'action': 'other'}, {'skill': 'other'}])
def test_mismatched_or_unverified_receipts_refused(spine, change):
    receipt = spine._kernel_append(Event(Kind.ACTION_RECEIPT, 'kernel', 'person-a',
        payload={'action': 'test', 'skill': 'skill', 'verified': True} | change))
    with pytest.raises(Refused, match='receipt'):
        spine.publish(claim(receipt.seq, receipt.seq))


@pytest.mark.parametrize('option', ['--lock', '--out'])
def test_outside_paths_refused_before_io(tmp_path, monkeypatch, option):
    monkeypatch.setattr(rt, 'STATE_DIR', tmp_path / 'state', raising=False)
    monkeypatch.setattr(rt, 'PRIVATE_TOOLS_DIR', tmp_path / 'private', raising=False)
    monkeypatch.setattr(rt, 'take_lock', lambda *_: pytest.fail('I/O before path validation'))
    with pytest.raises(ValueError, match='lab path'):
        rt.main([option, str(tmp_path / 'outside')])


def test_batch_cannot_spend_one_receipt_twice(spine):
    receipt = spine._kernel_append(Event(Kind.ACTION_RECEIPT, 'kernel', 'person-a',
        payload={'action': 'test', 'skill': 'skill', 'verified': True}))
    with pytest.raises(Refused, match='receipt'):
        spine.publish_many([claim(receipt.seq, receipt.seq)] * 2)
    assert not list(spine.replay('all', kinds={Kind.CLAIM}))
    good = spine.publish(claim(receipt.seq, receipt.seq))
    assert good.seq > receipt.seq
    with pytest.raises(Refused, match='receipt'):
        spine.publish(claim(receipt.seq, receipt.seq))


def test_claim_must_derive_from_receipt_so_forget_withdraws_it(spine):
    receipt = spine._kernel_append(Event(Kind.ACTION_RECEIPT, 'kernel', 'person-a',
        payload={'action': 'test', 'skill': 'skill', 'verified': True}))
    unrelated = spine.publish(Event(Kind.UTTERANCE, 'voice', 'person-a', payload={'text': 'synthetic'}))
    with pytest.raises(Refused, match='receipt'):
        spine.publish(claim(unrelated.seq, receipt.seq))
    backed = spine.publish(claim(receipt.seq, receipt.seq))
    spine.forget('person-a', actor='person-a', seqs=[receipt.seq])
    assert spine._get(backed.seq) is None


def test_control_disabling_claim_check_allows_unbacked_claim(spine, monkeypatch):
    cause = spine.publish(Event(Kind.UTTERANCE, 'voice', 'person-a', payload={'text': 'synthetic'}))
    monkeypatch.setattr(Spine, '_claim_refusal', lambda *_: '')
    assert spine.publish(claim(cause.seq)).kind is Kind.CLAIM
