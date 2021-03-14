import re
import subprocess
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

README_NAME = "README.md"
README_TEMPLATE = Path(__file__).parent / README_NAME
README_TARGET = Path(__file__).parents[1] / README_NAME

JINJA_ENV = Environment(
    loader=FileSystemLoader(README_TEMPLATE.parent), keep_trailing_newline=True
)


def generate_readme():
    template = JINJA_ENV.get_template(README_NAME)

    helptext = subprocess.check_output(["windowlayouts", "-h"]).decode().strip()
    match_home = re.search(r"/home/[a-zA-Z0-9]+", helptext)
    if match_home:
        helptext = helptext.replace(match_home[0], "~")
    readme_contents = template.render(helptext=helptext)

    with open(README_TARGET, "w") as readme_file:
        readme_file.write(readme_contents)


def main():
    generate_readme()


if __name__ == "__main__":
    main()
