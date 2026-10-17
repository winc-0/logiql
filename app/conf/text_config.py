from dataclasses import dataclass
from pathlib import Path

from omegaconf import OmegaConf

from app.conf.app_config import app_config


@dataclass
class FileConfig:
    enable: str
    level: str
# 定义结构化配置类
@dataclass
class TextConfig:
    name: str
    age: int
    height: float
    file: FileConfig


# 指定配置文件路径
# file_path =Path(__file__).parent.parent.parent/'conf'/'text_config.yaml'
file_path =Path(__file__).parents[2]/'conf'/'text_config.yaml'
# 加载yaml配置文件内容
context = OmegaConf.load(file_path)
# 指定配置内容的封装结构
schema = OmegaConf.structured(TextConfig)
# 合并并生成对象
text_config: TextConfig=OmegaConf.to_object(OmegaConf.merge(context,schema))

# print(text_config.file.level)


print(app_config.logging.console.enable)
