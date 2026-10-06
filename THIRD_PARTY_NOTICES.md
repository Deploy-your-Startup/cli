# Third-party software

Deploy Your Startup CLI's own code is licensed under the MIT license in
`LICENSE`. Dependencies retain their respective licenses.

## Ansible

The CLI invokes `ansible-vault` and `ansible-playbook` as separate executables.
It does not import Ansible's Python implementation. The CLI declares Ansible as
an installation dependency; its wheel does not bundle Ansible's implementation
or collections.

Ansible Core is licensed under GPL-3.0-or-later. See its
[license](https://github.com/ansible/ansible/blob/devel/COPYING) and the license
files accompanying the installed version. Collections may carry additional or
different licenses; consult each collection's metadata and license files.

Distributors who bundle Ansible or collections into an image, executable or
other deliverable must separately satisfy those components' license conditions,
including applicable notices and corresponding-source requirements. The CLI's
MIT license does not replace those conditions.
