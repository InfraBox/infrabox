export default class User {
    constructor (username, avatarUrl, name, email, githubId, gitlabId, id) {
        this.githubRepos = []
        this.gitlabRepos = []
        this.username = username
        this.avatarUrl = avatarUrl
        this.name = name
        this.email = email
        this.githubId = githubId
        this.gitlabId = gitlabId
        this.id = id
    }

    hasGithubAccount () {
        return this.githubId != null
    }

    hasGitlabAccount () {
        return this.gitlabId != null
    }

    isAdmin () {
        return this.id === '00000000-0000-0000-0000-000000000000'
    }
}
