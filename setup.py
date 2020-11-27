from setuptools import setup, find_packages

with open('Readme.md') as f:
    readme = f.read()

with open('requirements.txt') as f:
    requirements = f.read().splitlines()

# with open('LICENSE') as f:
#     license = f.read()

setup(
    name='batrack',
    version='0.1.0',
    description='Sense and record bats based on visuals, audio and VHF signals',
    long_description=readme,
    author='Patrick Lampe',
    author_email='lampep@mathematik.uni-marburg.de',
    url='https://github.com/Nature40/BatRack/',
    install_requires=requirements,
    # license=license,
    packages=find_packages(exclude=('tests', 'docs', 'etc')),
)
