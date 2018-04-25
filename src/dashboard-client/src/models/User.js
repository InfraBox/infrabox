export default class User {
    constructor (username, avatarUrl, name, email, githubId, gitlabId) {
        this.githubRepos = []
        this.gitlabRepos = []
        this.username = username
        this.avatarUrl = avatarUrl
        this.name = name
        this.email = email
        this.githubId = githubId
        this.gitlabId = gitlabId
    }

    hasGithubAccount () {
        return this.githubId != null
    }

    hasGitlabAccount () {
        return this.gitlabId != null
    }
}
