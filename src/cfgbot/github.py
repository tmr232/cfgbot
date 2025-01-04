def get_code_url(project:str, ref:str, filename:str, line:int)->str:
    return f"https://github.com/{project}/blob/{ref}/{filename}#L{line}"

def get_raw_url(project:str, ref:str, filename:str)->str:
    return f"https://raw.githubusercontent.com/{project}/{ref}/{filename}"


def get_project_url(project:str)->str:
    return f"https://github.com/{project}"