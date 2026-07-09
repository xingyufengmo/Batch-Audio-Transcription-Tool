# 打包说明

在开发电脑上运行：

```powershell
.\distribute\build.ps1
```

打包完成后会生成：

```text
release\AliyunASRTranslator\
release\AliyunASRTranslator.zip
```

把 `release\AliyunASRTranslator.zip` 发给别人即可。对方电脑不需要安装 Python。

注意：打包目录里的 `config.json` 来自模板，不包含你的真实密钥。分发前可以让对方自行填写，或你手动填好后再压缩发送。
