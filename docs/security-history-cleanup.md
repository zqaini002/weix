# 清理 GitHub 历史中的敏感数据

仅删除当前文件、添加 `.gitignore` 或在 GitHub 网页上删除文件，旧提交仍可能保留内容。对已经泄露的 API key、密码或令牌，先在对应服务撤销并换新；历史清理不能让已被复制的凭据重新安全。

## 此仓库需要检查的范围

历史中出现过 `config/config.yaml`、`data/all_keys.json`、`backend/data/all_keys.json`、聊天数据库、知识库数据和截图。公开检查记录只列路径，不展示密钥、聊天内容或账号 ID。

当前 `.env`、`.env.*`（保留 `.env.example`）、本地配置和运行时 `data/` 已忽略；`.gitignore` 不会清除这些文件的旧提交。

## 清理流程

1. 撤销已泄露的凭据，更新各电脑本机配置。
2. 先完成待推送的正常代码更新，暂停其他协作者推送；在私有、本机位置保存必要备份，备份也包含敏感历史，不要上传。
3. 在独立目录创建仓库镜像，不在正在使用的工作目录执行清理。以下命令是操作示例，强制推送必须先审阅清理结果。

```powershell
python -m pip install --upgrade git-filter-repo
git clone --mirror git@github.com:zqaini002/weix.git weix-history-cleanup.git
cd weix-history-cleanup.git

git filter-repo --sensitive-data-removal --invert-paths `
  --path .env --path .env.local `
  --path .env.production --path .env.development `
  --path-glob '.env.*.local' `
  --path config/config.yaml `
  --path data/ --path backend/data/
```

使用支持 `--sensitive-data-removal` 的 git-filter-repo（至少 2.47）。如果敏感文件曾改名或移到别处，需要补齐所有历史路径；如果凭据还散落在代码或其他文件中，需要另外用 `--replace-text` 清理。替换规则文件保存在仓库外，不在命令行或公开日志展示密钥。

4. 扫描重写后的所有分支和标签，核对正常代码仍保留。记录工具输出中的 First Changed Commit、受影响 PR 和孤立 LFS 对象。完成核对后才执行以下步骤。

```powershell
git remote -v
# 如果 filter-repo 移除了 origin，则重新添加；仍存在时不要重复添加。
git remote add origin git@github.com:zqaini002/weix.git

# 只有确认镜像里的所有引用都应覆盖远端后执行。
git push --force --mirror origin
```

这是全仓库历史改写，会更改提交 SHA，并覆盖镜像中的分支、标签和引用；如果清理期间有人推送新提交，会覆盖其更新。不要在普通工作副本中直接运行 `--mirror` 推送。受保护分支可能阻止强推，GitHub 的 `refs/pull/` 只读引用不能通过此命令清理。

5. 所有协作者和其他电脑重新克隆。不要把旧分支直接合并或推回新仓库，否则可能重新引入泄露历史。将本机 `.env` 和用户配置从私有备份恢复，数据库密钥仍在本机提取或验证。
6. 若敏感信息仍可从旧提交链接、PR、缓存或 LFS 访问，按 GitHub 文档联系 GitHub Support，并提供仓库名、First Changed Commit 和受影响引用。GitHub Support 对清理有条件限制，不能保证移除所有副本。

Fork、别人已经克隆的副本以及历史发布的 Release 附件需要分别处理，历史改写不会自动删除它们。

官方说明：[GitHub：从存储库中删除敏感数据](https://docs.github.com/zh/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)。
