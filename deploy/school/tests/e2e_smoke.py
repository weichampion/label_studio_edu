"""End-to-end smoke test of the school edition against a running server (about 146 checks).

It creates users, projects, annotations and changes grants, so run it ONLY against a server that uses a
disposable copy of the data directory (see deploy/school README / school_tenancy README), e.g.:

    python deploy/school/tests/e2e_smoke.py http://127.0.0.1:8090

Requires the demo data from ``manage.py school_seed_demo`` (password Demo@2026) and the multimodal demo project #4.
"""

import re
import sys
import uuid

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8090'
PASSWORD = 'Demo@2026'
DENIED = (403, 404)
TEXT_CONFIG = (
    '<View><Text name="text" value="$text"/>'
    '<Choices name="sentiment" toName="text"><Choice value="A"/></Choices></View>'
)
IMAGE_CONFIG = (
    '<View><Image name="image" value="$image"/>'
    '<RectangleLabels name="box" toName="image"><Label value="Car"/></RectangleLabels></View>'
)
results = []


def check(name, condition, detail=''):
    results.append((bool(condition), name, detail))
    print(('PASS ' if condition else 'FAIL ') + name + ('' if condition else f'  -> {detail}'), flush=True)


class Client:
    def __init__(self, email, password=PASSWORD):
        self.email = email
        self.s = requests.Session()
        self.s.get(f'{BASE}/user/login/')
        r = self.s.post(
            f'{BASE}/user/login/',
            data={'email': email, 'password': password, 'csrfmiddlewaretoken': self.csrf},
            headers={'Referer': f'{BASE}/user/login/'},
            allow_redirects=False,
        )
        self.logged_in = r.status_code == 302 and 'sessionid' in self.s.cookies

    @property
    def csrf(self):
        return self.s.cookies.get('csrftoken', '')

    def api(self, method, path, json=None, **kw):
        headers = {'X-CSRFToken': self.csrf, 'Referer': BASE + '/'}
        return self.s.request(method, BASE + path, json=json, headers=headers, allow_redirects=False, **kw)

    def form(self, path, data):
        data = dict(data, csrfmiddlewaretoken=self.csrf)
        return self.s.post(BASE + path, data=data, headers={'Referer': BASE + '/school/'}, allow_redirects=False)

    def project_ids(self):
        r = self.api('GET', '/api/projects/?page_size=100')
        return {p['id'] for p in r.json()['results']} if r.status_code == 200 else r.status_code

    def task_ids(self, project_id):
        r = self.api('GET', f'/api/tasks/?project={project_id}&page_size=100')
        return [t['id'] for t in r.json()['tasks']]

    def page(self, path):
        return self.s.get(BASE + path, allow_redirects=False)


def annotation(from_name='sentiment', to_name='text', value=None):
    return {
        'result': [
            {'from_name': from_name, 'to_name': to_name, 'type': 'choices', 'value': {'choices': [value or '正面']}}
        ]
    }


BOX = {
    'result': [
        {
            'id': 'e2e-box',
            'from_name': 'box',
            'to_name': 'image',
            'type': 'rectanglelabels',
            'original_width': 200,
            'original_height': 150,
            'value': {'x': 10, 'y': 10, 'width': 30, 'height': 30, 'rotation': 0, 'rectanglelabels': ['圆形']},
        }
    ]
}


def grant(teacher, project_id, student_id, features, modules=None):
    data = {'features': features}
    if modules is not None:
        data.update({'modules_submitted': '1', 'modules': modules})
    return teacher.form(f'/school/projects/{project_id}/students/{student_id}/features/', data)


def main():
    health = requests.get(f'{BASE}/health/')
    check('health endpoint UP', health.status_code == 200 and 'UP' in health.text, health.text)

    # --- 1. Login and roles --------------------------------------------------------------------
    emails = {
        'cs_admin': 'cs.admin@demo.edu.cn',
        'cs_teacher': 'cs.teacher@demo.edu.cn',
        's1': 'cs.student1@demo.edu.cn',
        's2': 'cs.student2@demo.edu.cn',
        'med_admin': 'med.admin@demo.edu.cn',
        'med_teacher': 'med.teacher@demo.edu.cn',
        'med_s1': 'med.student1@demo.edu.cn',
        'shared': 'shared.teacher@demo.edu.cn',
        'platform': 'platform.admin@demo.edu.cn',
    }
    c = {key: Client(email) for key, email in emails.items()}
    for key, client in c.items():
        check(f'login {client.email}', client.logged_in)
    check('wrong password rejected', not Client(emails['s1'], 'wrong-password').logged_in)

    expected_roles = {
        'cs_admin': 'college_admin', 'cs_teacher': 'teacher', 's1': 'student', 's2': 'student',
        'med_admin': 'college_admin', 'med_teacher': 'teacher', 'med_s1': 'student',
    }
    me = {}
    for key, role in expected_roles.items():
        me[key] = c[key].api('GET', '/api/school/me/').json()
        actual = (me[key].get('active_college') or {}).get('role')
        check(f'role of {emails[key]} is {role}', actual == role, actual)
    cs_org = me['cs_admin']['active_college']['id']
    med_org = me['med_admin']['active_college']['id']
    whoami = c['s1'].api('GET', '/api/current-user/whoami').json()
    check('whoami exposes school_role for student', whoami.get('school_role') == 'student', whoami.get('school_role'))
    check('student whoami has no projects.create', 'projects.create' not in whoami.get('permissions', []))

    # Make sure shared.teacher starts in the computer college
    c['shared'].api('POST', '/api/school/switch-college/', {'organization_id': cs_org})

    # --- 2. Project visibility and tenant isolation ------------------------------------------
    expected_projects = {
        'cs_admin': {1, 2, 4}, 'cs_teacher': {1, 2, 4}, 's1': {1, 4}, 's2': {1, 2, 4},
        'med_admin': {3}, 'med_teacher': {3}, 'med_s1': {3}, 'shared': {1, 2},
    }
    for key, ids in expected_projects.items():
        actual = c[key].project_ids()
        check(f'{emails[key]} sees projects {sorted(ids)}', actual == ids, actual)
    platform_projects = c['platform'].project_ids()
    check('platform admin sees no college projects', platform_projects in (set(), 403), platform_projects)
    check('platform admin can open /admin/', c['platform'].page('/admin/').status_code == 200)

    cs_task = c['cs_teacher'].task_ids(1)[0]
    med_task = c['med_teacher'].task_ids(3)[0]
    for key, path in (
        ('med_teacher', '/api/projects/1/'),
        ('med_teacher', f'/api/tasks/{cs_task}/'),
        ('med_teacher', '/api/projects/1/export?exportType=JSON'),
        ('med_admin', '/api/projects/4/'),
        ('cs_teacher', '/api/projects/3/'),
        ('cs_teacher', f'/api/tasks/{med_task}/'),
        ('s1', '/api/projects/2/'),
        ('s1', '/api/projects/3/'),
        ('med_s1', '/api/projects/1/'),
    ):
        status = c[key].api('GET', path).status_code
        check(f'{emails[key]} denied GET {path}', status in DENIED, status)
    status = c['med_teacher'].api('POST', f'/api/tasks/{cs_task}/annotations/', annotation()).status_code
    check('cross-college annotation write denied', status in DENIED, status)
    status = c['med_teacher'].api('POST', '/api/tasks/', {'project': 1, 'data': {'text': 'x'}}).status_code
    check('cross-college task creation denied', status in DENIED + (400,), status)
    status = c['med_teacher'].api('GET', '/api/tasks/?project=1').status_code
    check('cross-college task list denied', status in DENIED, status)
    users = c['s1'].api('GET', '/api/users/').json()
    check('student user list only contains self', [u['email'] for u in users] == [emails['s1']], users)

    # --- 3. College switching ------------------------------------------------------------------
    r = c['shared'].api('POST', '/api/school/switch-college/', {'organization_id': med_org})
    check('shared teacher switches to medical college', r.status_code == 200, r.status_code)
    check('after switch sees medical projects only', c['shared'].project_ids() == {3}, c['shared'].project_ids())
    check('after switch denied computer project', c['shared'].api('GET', '/api/projects/1/').status_code in DENIED)
    r = c['s1'].api('POST', '/api/school/switch-college/', {'organization_id': med_org})
    check('student cannot switch into a foreign college', r.status_code == 400, r.status_code)
    c['shared'].api('POST', '/api/school/switch-college/', {'organization_id': cs_org})
    check('switch back restores computer projects', c['shared'].project_ids() == {1, 2}, c['shared'].project_ids())

    # --- 4. Student defaults (annotation only) -------------------------------------------------
    s1, s2, teacher = c['s1'], c['s2'], c['cs_teacher']
    s1_id = whoami['id']
    s2_id = s2.api('GET', '/api/current-user/whoami').json()['id']
    project1 = s1.api('GET', '/api/projects/1/').json()
    check('student default features = annotate', project1.get('school_features') == ['annotate'], project1.get('school_features'))
    check('student cannot manage project', project1.get('school_can_manage') is False)
    for method, path, body in (
        ('GET', '/api/projects/1/export?exportType=JSON', None),
        ('POST', '/api/projects/1/import', [{'text': 'x'}]),
        ('PATCH', '/api/projects/1/', {'title': 'hacked'}),
        ('DELETE', '/api/projects/1/', None),
        ('POST', '/api/projects/', {'title': 'student project'}),
        ('DELETE', f'/api/tasks/{cs_task}/', None),
        ('PATCH', f'/api/tasks/{cs_task}/', {'data': {'text': 'changed'}}),
        ('GET', '/api/webhooks/', None),
        ('POST', '/api/dm/actions/?project=1&id=delete_tasks', {'selectedItems': {'all': True}}),
    ):
        status = s1.api(method, path, body).status_code
        check(f'student default denied {method} {path}', status in DENIED, status)
    check('student next task allowed', s1.api('GET', '/api/projects/1/next/').status_code == 200)
    tasks1 = s1.task_ids(1)
    r = s1.api('POST', f'/api/tasks/{tasks1[-1]}/annotations/', annotation(value='负面'))
    check('student submits annotation', r.status_code == 201, r.text[:200])
    s1_annotation = r.json().get('id')
    r = s1.api('PATCH', f'/api/annotations/{s1_annotation}/', annotation(value='正面'))
    check('student updates own annotation', r.status_code == 200, r.status_code)
    for method in ('PATCH', 'DELETE'):
        status = s2.api(method, f'/api/annotations/{s1_annotation}/', annotation() if method == 'PATCH' else None).status_code
        check(f'classmate {method} on others annotation denied', status in DENIED, status)
    check('teacher can update student annotation', teacher.api('PATCH', f'/api/annotations/{s1_annotation}/', annotation(value='负面')).status_code == 200)

    # --- 5. Teacher grants and revocations -----------------------------------------------------
    r = grant(teacher, 1, s1_id, ['annotate', 'export_data'])
    check('teacher grants export (form 302)', r.status_code == 302, r.status_code)
    check('granted student can export', s1.api('GET', '/api/projects/1/export?exportType=JSON').status_code == 200)
    check('other student still cannot export', s2.api('GET', '/api/projects/1/export?exportType=JSON').status_code in DENIED)
    check('grant does not leak to other project', s1.api('GET', '/api/projects/4/export?exportType=JSON').status_code in DENIED)
    dm = s1.api('GET', '/api/dm/project?project=1').json()
    check('data manager state reports export grant', 'export_data' in dm.get('school_features', []), dm.get('school_features'))
    grant(teacher, 1, s1_id, ['annotate', 'import_tasks'])
    check('revoked export takes effect immediately', s1.api('GET', '/api/projects/1/export?exportType=JSON').status_code in DENIED)
    r = s1.api('POST', '/api/projects/1/import', [{'text': 'e2e imported task'}])
    check('granted student can import', r.status_code == 201, r.text[:200])
    check('import grant does not allow project config', s1.api('PATCH', '/api/projects/1/', {'title': 'x'}).status_code in DENIED)
    grant(teacher, 1, s1_id, ['annotate', 'configure_project'])
    r = s1.api('PATCH', '/api/projects/1/', {'description': 'e2e', 'organization': med_org})
    check('configure grant allows editing settings', r.status_code == 200, r.text[:200])
    check('configure grant cannot move project college', teacher.api('GET', '/api/projects/1/').json()['organization'] == cs_org)
    check('configure grant cannot delete project', s1.api('DELETE', '/api/projects/1/').status_code in DENIED)
    status = s1.form(f'/school/projects/1/students/{s1_id}/features/', {'features': ['export_data']}).status_code
    check('student cannot grant permissions to self', status in DENIED, status)
    grant(teacher, 1, s1_id, ['manage_tasks'])
    new_task = s1.task_ids(1)[-1]
    check('manage_tasks grant allows task edit', s1.api('PATCH', f'/api/tasks/{new_task}/', {'data': {'text': 'edited'}}).status_code == 200)
    grant(teacher, 1, s1_id, [])
    check('read-only: project still visible', s1.api('GET', '/api/projects/1/').status_code == 200)
    status = s1.api('POST', f'/api/tasks/{tasks1[0]}/annotations/', annotation()).status_code
    check('read-only: annotation creation denied', status == 403, status)
    check('read-only: own annotation edit denied', s1.api('PATCH', f'/api/annotations/{s1_annotation}/', annotation()).status_code == 403)
    check('read-only: features empty', s1.api('GET', '/api/projects/1/').json()['school_features'] == [])
    grant(teacher, 1, s1_id, ['annotate'])
    check('restored annotation-only', s1.api('GET', '/api/projects/1/').json()['school_features'] == ['annotate'])

    # --- 6. Annotation modules -----------------------------------------------------------------
    full = s1.api('GET', '/api/projects/4/').json()
    check('default modules: no filtered config', full.get('school_label_config') is None, full.get('school_label_config'))
    page = teacher.page('/school/').text
    check('teacher form lists project modules', 'value="text"' in page and 'value="image"' in page)
    grant(teacher, 4, s1_id, ['annotate'], ['image'])
    restricted = s1.api('GET', '/api/projects/4/').json()
    config = restricted.get('school_label_config') or ''
    check('image-only config hides text controls', 'name="sentiment"' not in config and 'name="text"' not in config, config)
    check('image-only config keeps image controls', 'name="box"' in config and 'name="image"' in config, config)
    tasks4 = s1.task_ids(4)
    status = s1.api('POST', f'/api/tasks/{tasks4[0]}/annotations/', annotation()).status_code
    check('withheld module annotation denied', status == 403, status)
    status = s1.api('POST', f'/api/tasks/{tasks4[0]}/drafts', annotation()).status_code
    check('withheld module draft denied', status == 403, status)
    r = s1.api('POST', f'/api/tasks/{tasks4[0]}/annotations/', BOX)
    check('granted module annotation accepted', r.status_code == 201, r.text[:200])
    check('other student keeps all modules', s2.api('GET', '/api/projects/4/').json().get('school_label_config') is None)
    check('other student can annotate text module', s2.api('POST', f'/api/tasks/{tasks4[1]}/annotations/', annotation()).status_code == 201)
    grant(teacher, 4, s1_id, ['annotate'], [])
    check('no module => annotate removed', 'annotate' not in s1.api('GET', '/api/projects/4/').json()['school_features'])
    status = grant(teacher, 4, s1_id, ['annotate'], ['video']).status_code
    check('unknown module rejected (no change)', 'annotate' not in s1.api('GET', '/api/projects/4/').json()['school_features'], status)
    grant(teacher, 4, s1_id, ['annotate'], ['text', 'image'])
    check('all modules restores default', s1.api('GET', '/api/projects/4/').json().get('school_label_config') is None)

    # --- 6b. Students creating projects (module-limited grant) ---------------------------------
    check('student cannot create project without grant', s1.api('POST', '/api/projects/', {'title': 'e2e own'}).status_code == 403)
    html = teacher.page('/school/').text
    match = re.search(
        rf'{re.escape(emails["s1"])}</td>\s*<td>\s*<form method="post" action="/school/members/(\d+)/create-templates/"', html
    )
    check('teacher workbench shows student create-permission form', match)
    if match:
        create_url = f'/school/members/{match.group(1)}/create-templates/'
        templates = s1.api('GET', '/api/templates/').json()
        check('ungranted student sees no template tabs', templates.get('groups') == [] and templates.get('school_custom_template') is False, templates.get('groups'))
        check('teacher grants Computer Vision tab', teacher.form(create_url, {'groups': ['Computer Vision']}).status_code == 302)
        templates = s1.api('GET', '/api/templates/').json()
        check('student sees only the Computer Vision tab', templates.get('groups') == ['Computer Vision'], templates.get('groups'))
        check('student has no Custom template', templates.get('school_custom_template') is False)
        check('teacher still sees every tab', len(teacher.api('GET', '/api/templates/').json()['groups']) > 5)
        perms = s1.api('GET', '/api/current-user/whoami').json()['permissions']
        check('granted student gets projects.create', 'projects.create' in perms)
        status = s1.api('POST', '/api/projects/', {'title': 'e2e text', 'label_config': TEXT_CONFIG}).status_code
        check('text config denied with Computer Vision grant', status == 403, status)
        r = s1.api('POST', '/api/projects/', {'title': 'e2e own', 'label_config': IMAGE_CONFIG})
        check('student creates image project', r.status_code == 201, r.text[:200])
        own = r.json().get('id')
        body = s1.api('GET', f'/api/projects/{own}/').json()
        check('owner has every project feature', len(body.get('school_features') or []) == 6, body.get('school_features'))
        check('owner gains no teacher authority', body.get('school_can_manage') is False)
        status = s1.api('PATCH', f'/api/projects/{own}/', {'label_config': TEXT_CONFIG}).status_code
        check('owner cannot switch own project to withheld module', status == 403, status)
        for key, visible in (('cs_teacher', True), ('cs_admin', True), ('s2', False), ('med_teacher', False)):
            status = c[key].api('GET', f'/api/projects/{own}/').status_code
            check(f'{emails[key]} {"sees" if visible else "cannot see"} student project', (status == 200) == visible, status)
        check('owner deletes own project', s1.api('DELETE', f'/api/projects/{own}/').status_code == 204)
        teacher.form(create_url, {})
        status = s1.api('POST', '/api/projects/', {'title': 'e2e again'}).status_code
        check('revoked create grant denies creation', status == 403, status)

    # --- 7. Teacher scope, member management and invites --------------------------------------
    admin = c['cs_admin']
    new_teacher_email = f'e2e-teacher-{uuid.uuid4().hex[:6]}@demo.edu.cn'
    status = teacher.form('/school/members/', {'email': 'x@demo.edu.cn', 'role': 'teacher', 'password': 'Xx!23456789'}).status_code
    check('ordinary teacher cannot add members', status == 403, status)
    r = admin.form('/school/members/', {'email': new_teacher_email, 'name': 'E2E', 'password': 'E2e!Passw0rd#', 'role': 'teacher'})
    check('college admin adds teacher', r.status_code == 302, r.status_code)
    new_teacher = Client(new_teacher_email, 'E2e!Passw0rd#')
    check('new teacher logs in', new_teacher.logged_in)
    check('new teacher sees no projects yet', new_teacher.project_ids() == set(), new_teacher.project_ids())
    status = grant(new_teacher, 1, s1_id, ['annotate', 'export_data']).status_code
    check('new teacher cannot grant on others project', status in DENIED, status)
    r = new_teacher.api('POST', '/api/projects/', {'title': 'E2E teacher project'})
    check('teacher creates project', r.status_code == 201, r.text[:200])
    created = r.json().get('id')
    check('created project in own college', new_teacher.api('GET', f'/api/projects/{created}/').json().get('organization') == cs_org)
    check('creator teacher project hidden from other teacher', teacher.api('GET', f'/api/projects/{created}/').status_code in DENIED)
    check('college admin sees all college projects', created in admin.project_ids())
    admin.form('/school/projects/1/teachers/', {'email': new_teacher_email})
    check('designated teacher sees project 1', 1 in new_teacher.project_ids(), new_teacher.project_ids())
    check('designated teacher can grant', grant(new_teacher, 1, s1_id, ['annotate']).status_code == 302)
    admin.form('/school/projects/1/teachers/', {'email': new_teacher_email, 'remove': '1'})
    check('undesignated teacher loses project 1', 1 not in new_teacher.project_ids())

    html = admin.page('/school/').text
    match = re.search(rf'{re.escape(new_teacher_email)}</td>.*?/school/members/(\d+)/role/', html, re.S)
    check('member row found on admin workbench', match)
    if match:
        member_id = match.group(1)
        admin.form(f'/school/members/{member_id}/remove/', {})
        status = new_teacher.api('GET', '/api/projects/').status_code
        check('removed member loses API access', status == 403, status)

    token = re.search(r'/user/signup/\?token=([\w-]+)', html)
    check('invite link shown to college admin', token)
    bogus = requests.Session()
    bogus.get(f'{BASE}/user/signup/?token=not-a-real-token')
    r = bogus.post(
        f'{BASE}/user/signup/?token=not-a-real-token',
        data={'email': 'bogus@demo.edu.cn', 'password': 'E2e!Passw0rd#', 'csrfmiddlewaretoken': bogus.cookies.get('csrftoken')},
        headers={'Referer': BASE + '/user/signup/'},
        allow_redirects=False,
    )
    check('invalid invite token rejected', r.status_code == 403, r.status_code)
    check('invalid invite created no account', not Client('bogus@demo.edu.cn', 'E2e!Passw0rd#').logged_in)
    if token:
        student_email = f'e2e-student-{uuid.uuid4().hex[:6]}@demo.edu.cn'
        signup = requests.Session()
        signup.get(f'{BASE}/user/signup/?token={token.group(1)}')
        r = signup.post(
            f'{BASE}/user/signup/?token={token.group(1)}',
            data={'email': student_email, 'password': 'E2e!Passw0rd#', 'csrfmiddlewaretoken': signup.cookies.get('csrftoken')},
            headers={'Referer': BASE + '/user/signup/'},
            allow_redirects=False,
        )
        check('invite signup succeeds', r.status_code == 302, r.status_code)
        invited = Client(student_email, 'E2e!Passw0rd#')
        role = (invited.api('GET', '/api/school/me/').json().get('active_college') or {})
        check('invited user is student of computer college', role.get('role') == 'student' and role.get('id') == cs_org, role)
        check('invited student sees no projects', invited.project_ids() == set(), invited.project_ids())
        teacher.form('/school/projects/2/students/', {'emails': student_email})
        check('assigned invited student sees project 2', invited.project_ids() == {2}, invited.project_ids())

    # --- 8. Pages render for every role ---------------------------------------------------------
    for key in ('cs_admin', 'cs_teacher', 's1', 'med_s1', 'shared'):
        for path in ('/', '/projects/', '/school/', '/user/account/'):
            status = c[key].page(path).status_code
            check(f'{emails[key]} page {path}', status == 200, status)
    for key, project in (('s1', 1), ('cs_teacher', 4), ('med_s1', 3)):
        status = c[key].page(f'/projects/{project}/data').status_code
        check(f'{emails[key]} opens project {project} data page', status == 200, status)
    workbench_s1 = s1.page('/school/').text
    check('student workbench shows own permissions', '我的项目与已授权功能' in workbench_s1 and '项目与学生分配' not in workbench_s1)
    check('student workbench hides member management', '成员管理' not in workbench_s1)
    check('admin workbench has member management', '成员管理' in admin.page('/school/').text)
    check('teacher workbench hides member management', '成员管理' not in teacher.page('/school/').text)
    status = c['platform'].page('/school/').status_code
    check('platform admin without college redirected', status == 302, status)
    check('anonymous user redirected from workbench', requests.get(f'{BASE}/school/', allow_redirects=False).status_code == 302)
    check('anonymous API denied', requests.get(f'{BASE}/api/projects/').status_code in (401, 403))

    failed = [r for r in results if not r[0]]
    print(f'\nTOTAL {len(results)}  PASSED {len(results) - len(failed)}  FAILED {len(failed)}')
    for _, name, detail in failed:
        print(f'  FAILED: {name} -> {detail}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
