# Windows GitHub 多账号 SSH 配置

## 一、适用场景

本文档用于在同一台 Windows 电脑上同时使用多个 GitHub 账号，并能够按仓库选择不同账号进行认证和推送。

推荐方案：

1. 认证统一使用 `SSH`
2. 不同账号使用不同私钥
3. 在 `~/.ssh/config` 中为每个账号配置独立 `Host` 别名
4. 每个仓库通过不同的远程地址选择对应账号

`ssh-agent` 不是本方案的前置条件。不启动 `ssh-agent` 也可以使用，只是带口令的私钥可能需要重复输入口令。

## 二、目录与文件约定

Windows 下，SSH 目录通常位于：

```text
%USERPROFILE%\.ssh
```

推荐准备以下文件：

1. `id_ed25519_github_a`
2. `id_ed25519_github_a.pub`
3. `id_ed25519_github_b`
4. `id_ed25519_github_b.pub`
5. `known_hosts`
6. `config`

其中：

1. `id_ed25519_github_a` 对应 GitHub 账号 A
2. `id_ed25519_github_b` 对应 GitHub 账号 B
3. `config` 用于定义 SSH Host 别名
4. `known_hosts` 用于保存已信任主机的指纹

## 三、生成密钥

如果还没有私钥，可以执行：

```powershell
ssh-keygen -t ed25519 -C "github-account-a" -f $env:USERPROFILE\.ssh\id_ed25519_github_a
ssh-keygen -t ed25519 -C "github-account-b" -f $env:USERPROFILE\.ssh\id_ed25519_github_b
```

说明：

1. `-C` 只是注释标签，不决定认证身份
2. 文件名可以自定义，但建议有清晰区分
3. 如果已经有密钥，不需要重复生成

## 四、SSH 配置文件内容

文件路径：

```text
%USERPROFILE%\.ssh\config
```

注意：

1. 文件名就是 `config`
2. 没有扩展名
3. 如果本机原先已有其他 SSH 配置，需要把以下内容合并进去，而不是覆盖无关配置

配置示例：

```sshconfig
Host github-account-a
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_github_a
    IdentitiesOnly yes

Host github-account-b
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_github_b
    IdentitiesOnly yes
```

配置含义：

1. `github-account-a` 固定使用账号 A 的私钥
2. `github-account-b` 固定使用账号 B 的私钥
3. 仓库只要使用不同的 SSH Host 别名，就能切换 GitHub 账号

## 五、公钥绑定规则

必须把公钥添加到对应 GitHub 账号，否则 SSH 认证不会成功。

对应关系如下：

1. `id_ed25519_github_a.pub` 添加到 GitHub 账号 A
2. `id_ed25519_github_b.pub` 添加到 GitHub 账号 B

查看公钥内容的命令：

```powershell
Get-Content $env:USERPROFILE\.ssh\id_ed25519_github_a.pub
Get-Content $env:USERPROFILE\.ssh\id_ed25519_github_b.pub
```

## 六、Windows OpenSSH 权限修复

在 Windows 下，如果 `.ssh`、`config`、私钥文件或 `known_hosts` 的权限过宽，OpenSSH 可能报错：

1. `Bad owner or permissions`
2. `Bad permissions`
3. `known_hosts: Permission denied`

可以使用以下 PowerShell 命令修复：

```powershell
$sshDir = Join-Path $env:USERPROFILE '.ssh'
$configFile = Join-Path $sshDir 'config'
$knownHostsFile = Join-Path $sshDir 'known_hosts'
$keyA = Join-Path $sshDir 'id_ed25519_github_a'
$keyB = Join-Path $sshDir 'id_ed25519_github_b'
$account = "$env:USERDOMAIN\$env:USERNAME"

& icacls $sshDir /inheritance:r /grant:r "${account}:F" 'SYSTEM:F' 'BUILTIN\Administrators:F'
& icacls $configFile /inheritance:r /grant:r "${account}:F" 'SYSTEM:F' 'BUILTIN\Administrators:F'
& icacls $keyA /inheritance:r /grant:r "${account}:F" 'SYSTEM:F' 'BUILTIN\Administrators:F'
& icacls $keyB /inheritance:r /grant:r "${account}:F" 'SYSTEM:F' 'BUILTIN\Administrators:F'
& icacls $knownHostsFile /inheritance:r /grant:r "${account}:F" 'SYSTEM:F' 'BUILTIN\Administrators:F'

& icacls $sshDir /setowner $account
& icacls $configFile /setowner $account
& icacls $keyA /setowner $account
& icacls $keyB /setowner $account
& icacls $knownHostsFile /setowner $account
```

说明：

1. 上述脚本会自动使用当前 Windows 登录用户
2. 如果只使用一个账号，可以删除不需要的密钥相关行
3. 如果文件不存在，需要先创建对应文件再执行

## 七、验证命令

验证账号 A：

```powershell
ssh -T git@github-account-a
```

验证账号 B：

```powershell
ssh -T git@github-account-b
```

认证成功时，会看到类似输出：

```text
Hi ACCOUNT_NAME! You've successfully authenticated, but GitHub does not provide shell access.
```

如果第一次连接出现主机指纹确认，输入：

```text
yes
```

## 八、仓库远程地址写法

属于账号 A 的仓库，远程地址写法：

```text
git@github-account-a:ACCOUNT_A/REPO.git
```

属于账号 B 的仓库，远程地址写法：

```text
git@github-account-b:ACCOUNT_B/REPO.git
```

切换当前仓库远程的示例：

```powershell
git remote set-url origin git@github-account-a:ACCOUNT_A/REPO.git
```

如果仓库还有其他远程，也可以按同样方式设置：

```powershell
git remote set-url upstream git@github-account-b:ACCOUNT_B/REPO.git
```

## 九、推送命令

完成 SSH 认证后，正常推送即可：

```powershell
git push -u origin main
```

如果默认分支不是 `main`，请替换为实际分支名。

## 十、提交身份与推送身份的区别

需要明确区分以下两件事：

1. `git config user.name`
2. `git config user.email`
3. SSH 远程地址和 SSH 私钥

其中：

1. `user.name` 和 `user.email` 只影响提交记录显示的作者信息
2. 是否能推送成功，取决于当前远程地址使用了哪一个 SSH Host 别名，以及该别名绑定的私钥是否对目标仓库有权限

也就是说：

1. 作者名正确，不代表一定有推送权限
2. 推送权限正确，也不代表提交作者一定正确

## 十一、推荐使用规则

建议长期遵循以下规则：

1. 多 GitHub 账号场景优先使用 `SSH`，不建议在不同账号间混用 `HTTPS`
2. 每个账号使用独立私钥
3. 每个仓库的远程地址显式写成对应的 SSH Host 别名
4. 每个仓库单独设置 `git config user.name` 和 `git config user.email`

这样可以同时解决两个问题：

1. 推送身份正确
2. 提交作者正确
