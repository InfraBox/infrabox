import os

if os.environ['INFRABOX_ACCOUNT_SIGNUP_ENABLED'] == 'true':
    import api.handlers.account.account
elif os.environ['INFRABOX_ACCOUNT_LDAP_ENABLED'] == 'true':
    import api.handlers.account.account_ldap

if os.environ['INFRABOX_GITHUB_ENABLED'] == 'true':
    import api.handlers.account.github

if os.environ['INFRABOX_GITLAB_ENABLED'] == 'true':
    import api.handlers.account.gitlab
