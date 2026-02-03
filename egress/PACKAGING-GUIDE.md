# Jenkins Tools - Packaging Guide

This guide explains how to package `generate-envoy-config.py` as a Python module with the bundled `envoy.yaml.j2` template.

---

## Package Structure

Your `jenkins_tools` package should have this structure:

```
jenkins_tools/
├── __init__.py
├── generate_envoy_config.py       # Main script (renamed with underscore)
├── config/
│   ├── __init__.py                # Makes config a package
│   └── envoy.yaml.j2              # Bundled template
├── setup.py                       # Package configuration
├── MANIFEST.in                    # Include non-Python files
├── README.md                      # Package documentation
└── requirements.txt               # Dependencies
```

---

## Step-by-Step Setup

### 1. Create the Package Directory

```bash
mkdir -p jenkins_tools/config
cd jenkins_tools
```

### 2. Create `__init__.py` Files

**`jenkins_tools/__init__.py`:**
```python
"""Jenkins CI/CD tools for Envoy configuration generation."""

__version__ = "1.0.0"
```

**`jenkins_tools/config/__init__.py`:**
```python
"""Configuration templates for jenkins_tools."""
```

### 3. Copy the Script

```bash
# Copy and rename (use underscore, not hyphen)
cp /path/to/generate-envoy-config.py jenkins_tools/generate_envoy_config.py
```

### 4. Copy the Template

```bash
cp /path/to/envoy.yaml.j2 jenkins_tools/config/envoy.yaml.j2
```

### 5. Create `setup.py`

See `setup.py.example` in this directory, or use this minimal version:

```python
from setuptools import setup, find_packages

setup(
    name="jenkins_tools",
    version="1.0.0",
    packages=find_packages(),
    include_package_data=True,
    package_data={
        "jenkins_tools": ["config/*.j2"],
    },
    install_requires=[
        "pyyaml>=5.4",
        "jinja2>=3.0",
    ],
    entry_points={
        "console_scripts": [
            "generate-envoy-config=jenkins_tools.generate_envoy_config:main",
        ],
    },
)
```

### 6. Create `MANIFEST.in`

```
include jenkins_tools/config/*.j2
include README.md
global-exclude __pycache__
global-exclude *.py[co]
```

### 7. Create `requirements.txt`

```
pyyaml>=5.4
jinja2>=3.0
```

---

## Installation

### Development Installation (Editable)

```bash
# Install in editable mode (for development)
pip install -e .

# Or with development dependencies
pip install -e ".[dev]"
```

### Production Installation

```bash
# Build and install
pip install .

# Or build wheel for distribution
python setup.py bdist_wheel
pip install dist/jenkins_tools-1.0.0-py3-none-any.whl
```

### From Git Repository

```bash
pip install git+https://github.com/your-org/jenkins-tools.git
```

---

## Usage

### 1. As a Python Module

After installation, you can run the tool as a module:

```bash
# Uses bundled template automatically
python3 -m jenkins_tools.generate_envoy_config \
    --env prd \
    -a egress-allowlist.yaml \
    -o envoy.yaml

# With validation
python3 -m jenkins_tools.generate_envoy_config \
    --env prd \
    -a egress-allowlist.yaml \
    -o envoy.yaml \
    --validate
```

### 2. As a Console Command

If you included `entry_points` in `setup.py`:

```bash
# Command is now available globally
generate-envoy-config --env prd -a egress-allowlist.yaml -o envoy.yaml
```

### 3. With Custom Template (Override Bundled)

```bash
python3 -m jenkins_tools.generate_envoy_config \
    --env prd \
    -a egress-allowlist.yaml \
    -t /path/to/custom-template.j2 \
    -o envoy.yaml
```

---

## Jenkins Pipeline Integration

### Method 1: Install from Git

```groovy
pipeline {
    agent any
    
    stages {
        stage('Setup') {
            steps {
                sh '''
                    python3 -m venv venv
                    . venv/bin/activate
                    pip install git+https://github.com/your-org/jenkins-tools.git
                '''
            }
        }
        
        stage('Generate Envoy Config') {
            steps {
                sh '''
                    . venv/bin/activate
                    python3 -m jenkins_tools.generate_envoy_config \
                        --env ${ENV} \
                        -a egress-allowlist.yaml \
                        -o envoy.yaml
                '''
            }
        }
        
        stage('Validate Config') {
            steps {
                sh '''
                    docker run --rm -v $(pwd):/config envoyproxy/envoy:v1.28-latest \
                        --mode validate -c /config/envoy.yaml
                '''
            }
        }
    }
}
```

### Method 2: Install from Wheel

```groovy
pipeline {
    agent any
    
    stages {
        stage('Setup') {
            steps {
                sh '''
                    pip install jenkins-tools-1.0.0-py3-none-any.whl
                '''
            }
        }
        
        stage('Generate Config') {
            steps {
                sh '''
                    generate-envoy-config \
                        --env ${ENV} \
                        -a egress-allowlist.yaml \
                        -o envoy.yaml
                '''
            }
        }
    }
}
```

### Method 3: Pre-installed in Jenkins Agent

If the package is pre-installed on Jenkins agents:

```groovy
pipeline {
    agent any
    
    stages {
        stage('Generate Config') {
            steps {
                sh '''
                    python3 -m jenkins_tools.generate_envoy_config \
                        --env ${ENV} \
                        -a egress-allowlist.yaml \
                        -o envoy.yaml
                '''
            }
        }
    }
}
```

---

## Verification

### Check Package Contents

```bash
# List files in installed package
python3 -c "
import jenkins_tools
import os
print('Package location:', jenkins_tools.__file__)
pkg_dir = os.path.dirname(jenkins_tools.__file__)
for root, dirs, files in os.walk(pkg_dir):
    for file in files:
        print(os.path.join(root, file))
"
```

### Verify Template is Bundled

```bash
python3 -c "
import pkg_resources
template = pkg_resources.resource_filename('jenkins_tools', 'config/envoy.yaml.j2')
print('Template location:', template)
import os
print('Exists:', os.path.exists(template))
"
```

### Test Template Loading

```bash
python3 -c "
from jenkins_tools.generate_envoy_config import get_bundled_template_path
template = get_bundled_template_path()
print('Found template:', template)
"
```

---

## Dockerfile Example

Build a Docker image with jenkins_tools pre-installed:

```dockerfile
FROM python:3.11-slim

# Install jenkins_tools
COPY jenkins_tools/ /tmp/jenkins_tools/
RUN pip install /tmp/jenkins_tools && rm -rf /tmp/jenkins_tools

# Or install from wheel
# COPY jenkins-tools-1.0.0-py3-none-any.whl /tmp/
# RUN pip install /tmp/jenkins-tools-1.0.0-py3-none-any.whl

WORKDIR /workspace

# Usage: docker run -v $(pwd):/workspace image:tag \
#   python3 -m jenkins_tools.generate_envoy_config --env prd -a egress-allowlist.yaml
```

---

## Troubleshooting

### Template Not Found

**Error:**
```
ERROR: Template file not found: config/envoy.yaml.j2
```

**Solutions:**

1. **Check package_data in setup.py:**
   ```python
   package_data={
       "jenkins_tools": ["config/*.j2"],
   }
   ```

2. **Check MANIFEST.in:**
   ```
   include jenkins_tools/config/*.j2
   ```

3. **Reinstall with `--force-reinstall`:**
   ```bash
   pip install --force-reinstall .
   ```

4. **Use `include_package_data=True` in setup.py**

### Import Error

**Error:**
```
ModuleNotFoundError: No module named 'jenkins_tools'
```

**Solutions:**

1. Install the package:
   ```bash
   pip install -e .
   ```

2. Check PYTHONPATH:
   ```bash
   export PYTHONPATH=/path/to/jenkins_tools:$PYTHONPATH
   ```

### Wrong Template Version

If you update the template but the old version is still being used:

```bash
# Uninstall and reinstall
pip uninstall jenkins_tools
pip install -e .

# Or use --force-reinstall
pip install --force-reinstall .
```

---

## Best Practices

1. **Version Control**: Tag releases in Git
   ```bash
   git tag -a v1.0.0 -m "Release 1.0.0"
   git push origin v1.0.0
   ```

2. **Semantic Versioning**: Use MAJOR.MINOR.PATCH
   - MAJOR: Breaking changes
   - MINOR: New features (backward compatible)
   - PATCH: Bug fixes

3. **Testing**: Test the package before distribution
   ```bash
   # Build wheel
   python setup.py bdist_wheel
   
   # Test in clean virtualenv
   python3 -m venv test_env
   source test_env/bin/activate
   pip install dist/jenkins_tools-1.0.0-py3-none-any.whl
   
   # Test it works
   python3 -m jenkins_tools.generate_envoy_config --env dev -a test.yaml
   ```

4. **Private PyPI**: For internal use, host on private PyPI server
   ```bash
   pip install jenkins_tools --index-url https://pypi.your-company.com/simple
   ```

---

## Summary

| Scenario | Command |
|----------|---------|
| **Install for development** | `pip install -e .` |
| **Run with bundled template** | `python3 -m jenkins_tools.generate_envoy_config --env prd -a allowlist.yaml` |
| **Run with custom template** | `python3 -m jenkins_tools.generate_envoy_config --env prd -a allowlist.yaml -t custom.j2` |
| **Use as CLI command** | `generate-envoy-config --env prd -a allowlist.yaml` |
| **Verify template bundled** | `python3 -c "from jenkins_tools.generate_envoy_config import get_bundled_template_path; print(get_bundled_template_path())"` |

---

## Next Steps

1. Create the package structure
2. Copy files and create setup.py
3. Install and test locally
4. Deploy to Jenkins or Docker image
5. Update your Jenkinsfile to use the module

For questions or issues, refer to `EGRESS-ALLOWLIST-GUIDE.md` for configuration help.