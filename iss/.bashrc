# .bashrc

# Source global definitions
if [ -f /etc/bashrc ]; then
	. /etc/bashrc
fi

# User specific aliases and functions

PS1='\[\e[1;32m\]\n\u \[\e[1;31m\]\h\[\e[1;33m\] \w \[\e[1;36m\]\D{%F %T}\[\e[1;37m\]\n#\e[m'

if [ -f ~/.bash_aliases ]; then
    . ~/.bash_aliases
fi
