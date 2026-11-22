"""Fresh storage ownership only; no live Shopify or real appdata access."""
import json
from pathlib import Path
import pytest
from app import deployment_guard as guard
from test_unraid_develop_template import guarded_environment


@pytest.fixture
def fresh(guarded_environment,monkeypatch):
    role,instance,env=guarded_environment
    roots=guard.roots_for(instance,env)
    for path in roots.values():(path/guard.MARKER).unlink()
    # Exercise the real Linux mount parser without mounting host test directories.
    mounts=[f'{i+2} 1 0:{i+2} /volume-{key} {path} rw - ext4 /dev/fixture rw'
            for i,(key,path) in enumerate(roots.items())]
    original_file,original_read=Path.is_file,Path.read_text
    monkeypatch.setattr(Path,'is_file',lambda path:True if str(path)=='/proc/self/mountinfo' else original_file(path))
    monkeypatch.setattr(Path,'read_text',lambda path,*a,**k:'\n'.join(mounts) if str(path)=='/proc/self/mountinfo' else original_read(path,*a,**k))
    owner=guard.identity(env)
    def authenticated(config):
        assert all(not (path/guard.MARKER).exists() for path in roots.values())
        return owner.domain,owner.shop,owner.app,role=='development'
    return instance,env,roots,owner,authenticated,mounts


def test_fresh_initialisation_and_exact_restart(fresh):
    instance,env,roots,owner,authenticated,_=fresh
    assert guard.verify_startup(instance,env,verify_product=authenticated)[0]==owner
    before={}
    for role,path in roots.items():
        assert set(path.iterdir())=={path/guard.MARKER}
        assert json.loads((path/guard.MARKER).read_bytes())==owner.marker(role)
        assert (path/guard.MARKER).stat().st_mode & 0o777 == 0o644
        before[role]=(path/guard.MARKER).stat().st_ino
    # Subsequent legitimate business files are not mistaken for fresh storage.
    (roots['catalogue']/'retained-product.txt').write_text('existing catalogue data')
    assert guard.verify_startup(instance,env,verify_product=lambda _: (
        owner.domain,owner.shop,owner.app,owner.environment=='development'))[0]==owner
    assert before=={role:(path/guard.MARKER).stat().st_ino for role,path in roots.items()}


@pytest.mark.parametrize('case',['partial','wrong_environment','wrong_storage_id','wrong_role','symlink'])
def test_existing_marker_conflicts_are_never_adopted(fresh,case):
    instance,env,roots,owner,_,_=fresh
    if case!='partial':
        for role,root in roots.items():(root/guard.MARKER).write_text(json.dumps(owner.marker(role)))
    path=roots['catalogue']/guard.MARKER
    value=owner.marker('catalogue')
    if case=='wrong_environment':value['environment']='production' if owner.environment=='development' else 'development'
    if case=='wrong_storage_id':value['storage_id']='f'*32
    if case=='wrong_role':value['role']='instance'
    if case=='symlink':
        path.unlink();path.symlink_to(roots['instance']/'missing')
    else:path.write_text(json.dumps(value))
    before=path.read_bytes() if not path.is_symlink() else None
    with pytest.raises(guard.DeploymentError):
        guard.verify_startup(instance,env,verify_product=lambda _:pytest.fail('Conflicting storage must fail before remote access'))
    assert path.is_symlink() if case=='symlink' else path.read_bytes()==before
    assert (roots['instance']/guard.MARKER).exists() is (case!='partial')


@pytest.mark.parametrize('role,filename',[('instance','site.db'),('instance','commerce.json'),('catalogue','product_info.json'),('taxonomy','registry.json'),('intake','customer.jpg')])
def test_unmarked_existing_data_is_not_claimed(fresh,role,filename):
    instance,env,roots,owner,_,_=fresh
    path=roots[role]/filename;path.write_bytes(b'retained')
    with pytest.raises(guard.DeploymentError,match='not empty'):
        guard.verify_startup(instance,env,verify_product=lambda _:pytest.fail('Must not authenticate before rejecting existing data'))
    assert path.read_bytes()==b'retained'
    assert not any((p/guard.MARKER).exists() for p in roots.values())


def test_identity_and_authentication_must_pass_before_writing(fresh):
    instance,env,roots,owner,authenticated,_=fresh
    with pytest.raises(guard.DeploymentError):
        guard.verify_startup(instance,env|{'TLC_DEPLOYMENT_STORAGE_ID':'invalid'},verify_product=lambda _:pytest.fail('Invalid identity'))
    with pytest.raises(guard.DeploymentError):
        guard.verify_startup(instance,env,verify_product=lambda _:('wrong.myshopify.com',owner.shop,owner.app,False))
    with pytest.raises(guard.DeploymentError):
        guard.verify_startup(instance,env|{'SHOPIFY_STORE_DOMAIN':'wrong.myshopify.com'},verify_product=lambda _:pytest.fail('Invalid credentials destination'))
    assert not any((p/guard.MARKER).exists() for p in roots.values())


def test_discord_notifications_do_not_block_bootstrap_or_restart(fresh):
    instance,env,roots,owner,authenticated,_=fresh
    notifications={key:'https://discord.com/api/webhooks/fixture/not-a-real-token'
        for key in ('DISCORD_WEBHOOK_SCANS_ERRORS','DISCORD_WEBHOOK_OVERRIDES',
                    'DISCORD_WEBHOOK_EDITS','DISCORD_WEBHOOK_INGEST','DISCORD_WEBHOOK_SCANS_INFO')}
    env.update(notifications)
    assert guard.verify_startup(instance,env,verify_product=authenticated)[0]==owner
    assert guard.verify_startup(instance,env,verify_product=lambda _: (
        owner.domain,owner.shop,owner.app,owner.environment=='development'))[0]==owner
    assert {key:env[key] for key in notifications}==notifications


@pytest.mark.parametrize('case',['unmounted','read_only','aliased','nested'])
def test_mount_requirements(fresh,case):
    instance,env,roots,owner,_,mounts=fresh
    if case=='unmounted':mounts.pop()
    elif case=='read_only':mounts[-1]=mounts[-1].replace(' rw -',' ro -')
    elif case=='aliased':
        mounts[1]=mounts[1].replace('0:3','0:2').replace('/volume-catalogue','/volume-instance')
    else:mounts.append(f'99 1 0:99 /nested {roots["catalogue"]}/nested rw - ext4 /dev/fixture rw')
    with pytest.raises(guard.DeploymentError):
        guard.verify_startup(instance,env,verify_product=lambda _:pytest.fail('Unsafe mounts'))
    assert not any((p/guard.MARKER).exists() for p in roots.values())


def test_publication_failure_rolls_back_only_own_files(fresh,monkeypatch):
    instance,env,roots,owner,authenticated,_=fresh
    original=guard.os.link;calls=[]
    def failed(*args,**kwargs):
        calls.append(args)
        if len(calls)==2:raise OSError('Synthetic publication failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(guard.os,'link',failed)
    with pytest.raises(guard.DeploymentError,match='initialisation failed'):
        guard.verify_startup(instance,env,verify_product=authenticated)
    assert all(not list(path.iterdir()) for path in roots.values())


def test_data_arriving_during_authentication_is_not_claimed(fresh):
    instance,env,roots,owner,authenticated,_=fresh
    def raced(config):
        identity=authenticated(config)
        (roots['catalogue']/'arrived.json').write_text('retained')
        return identity
    with pytest.raises(guard.DeploymentError,match='not empty'):
        guard.verify_startup(instance,env,verify_product=raced)
    assert (roots['catalogue']/'arrived.json').read_text()=='retained'
    assert not any((p/guard.MARKER).exists() for p in roots.values())


def test_concurrent_starters_never_overwrite_markers(fresh):
    from concurrent.futures import ThreadPoolExecutor
    instance,env,roots,owner,_,_=fresh
    def start(_):
        try:
            guard.verify_startup(instance,env,verify_product=lambda _: (
                owner.domain,owner.shop,owner.app,owner.environment=='development'))
            return True
        except guard.DeploymentError:
            return False  # A concurrent/partial attempt must fail closed, not repair.
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(start,range(2)))
    assert any(results)
    for role,path in roots.items():
        assert set(path.iterdir())=={path/guard.MARKER}
        assert json.loads((path/guard.MARKER).read_bytes())==owner.marker(role)
