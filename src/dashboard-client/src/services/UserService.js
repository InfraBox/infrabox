import store from '../store'
import NewAPIService from './NewAPIService'
import ProjectService from './ProjectService'
import User from '../models/User'

class UserService {
    init () {
        this._loadSettings().then(() => {
            return this._loadUser()
        })
    }

    login () {
        this.init()
    }

    _loadSettings () {
        return NewAPIService.get(`settings/`)
            .then((s) => {
                console.log(s)
                store.commit('setSettings', s)
            })
    }

    _loadUser () {
        return NewAPIService.get(`user/`, true)
            .then((d) => {
                const u = new User(d.username,
                                   d.avatar_url,
                                   d.name,
                                   d.email,
                                   d.github_id,
                                   d.gitlab_id)
                store.commit('setUser', u)

                if (u.hasGithubAccount() && store.state.settings.INFRABOX_GITHUB_ENABLED) {
                    NewAPIService.get('github/repos/')
                    .then((d) => {
                        if (d) {
                            store.commit('setGithubRepos', d)
                        }
                    })
                }
                if (u.hasGitlabAccount() && store.state.settings.INFRABOX_GITLAB_ENABLED) {
                    NewAPIService.get('gitlab/projects/')
                    .then((d) => {
                        if (d) {
                            store.commit('setGitlabRepos', d)
                        }
                    })
                }
            })
            .then(() => {
                ProjectService.init()
            })
            .catch(() => {
                // ignore
            })
    }
}

export default new UserService()
