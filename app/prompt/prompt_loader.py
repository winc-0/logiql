from pathlib import Path

def load_prompt(name:str):
    # 定义路径
    prmpt_file =Path(__file__).parents[2]/"prompts"/f"{name}.prompt"
    # 读取数据
    return prmpt_file.read_text(encoding="utf_8")


if __name__ == '__main__':

    print(load_prompt("extend_keywords_for_column_recall"))
