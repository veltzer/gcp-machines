##############
# parameters #
##############
# do you want to see the commands executed ?
DO_MKDBG:=0
# do you want to check the javascript code?
DO_CHECKJS:=0
# do you want to validate html?
DO_CHECKHTML:=0
# do you want to validate css?
DO_CHECKCSS:=0
# do you want to check jinja files?
DO_CHECK_JINJA:=0
# do you want dependency on the makefile itself ?
DO_ALLDEP:=1
# do you want to check python code with pylint?
DO_PYLINT:=1
# do you want to check bash syntax?
DO_BASH_CHECK:=1

########
# code #
########
TOOL_COMPILER:=tools/closure-compiler-v20160822.jar
TOOL_JSMIN:=tools/jsmin
TOOL_CSS_VALIDATOR:=tools/css-validator/css-validator.jar
TOOL_JSL:=tools/jsl/jsl
TOOL_JSDOC:=node_modules/jsdoc/jsdoc.js
TOOL_JSLINT:=node_modules/jslint/bin/jslint.js
TOOL_GJSLINT:=/usr/bin/gjslint
TOOL_YUICOMPRESSOR:=/usr/bin/yui-compressor
TOOL_TIDY=/usr/bin/tidy
TOOL_CSSTIDY=/usr/bin/csstidy

CLEAN:=
ALL:=

PYTHON_SRC:=$(shell find scripts -type f -and -name "*.py")
PYTHON_LINT=$(addprefix out/, $(addsuffix .lint, $(basename $(PYTHON_SRC))))

BASH_SRC:=$(shell find scripts -type f -and -name "*.sh")
BASH_CHECK:=$(addprefix out/, $(addsuffix .check, $(basename $(BASH_SRC))))

SOURCES_JS:=$(shell pymakehelper no_err find src/js -type f -and -name "*.js" 2> /dev/null)
SOURCES_HTML:=$(shell pymakehelper no_err find src/html -type f -and -name "*.html" 2> /dev/null)
SOURCES_CSS:=$(shell pymakehelper no_err find src/css -type f -and -name "*.css" 2> /dev/null)
SOURCES_JINJA:=$(shell pymakehelper no_err find src/templates -type f -and -name "*.html" 2> /dev/null)

ifeq ($(DO_CHECK_JS),1)
endif # DO_CHECK_JS

ifeq ($(DO_CHECK_HTML),1)
endif # DO_CHECK_HTML

ifeq ($(DO_CHECK_CSS),1)
endif # DO_CHECK_CSS

ifeq ($(DO_CHECK_JINJA),1)
endif # DO_CHECK_JINJA

ifeq ($(DO_PYLINT),1)
ALL+=$(PYTHON_LINT)
CLEAN+=$(PYTHON_LINT)
endif # DO_PYLINT

ifeq ($(DO_BASH_CHECK),1)
ALL+=$(BASH_CHECK)
CLEAN+=$(BASH_CHECK)
endif # DO_BASH_CHECK

# silent stuff
ifeq ($(DO_MKDBG),1)
Q:=
# we are not silent in this branch
else # DO_MKDBG
Q:=@
#.SILENT:
endif # DO_MKDBG

#########
# rules #
#########
.PHONY: all
all: $(ALL)
	@true

.PHONY: pylint
pylint:
	$(Q)pymakehelper only_print_on_error python -m pylint --reports=n --score=n $(PYTHON_SRC)

.PHONY: debug
debug:
	$(info doing [$@])
	$(info SOURCES_JS is $(SOURCES_JS))
	$(info SOURCES_HTML is $(SOURCES_HTML))
	$(info SOURCES_CSS is $(SOURCES_CSS))
	$(info SOURCES_JINJA is $(SOURCES_JINJA))
.PHONY: clean
clean:
	$(info doing [$@])
	$(Q)-rm -f $(CLEAN)
.PHONY: clean_hard
clean_hard:
	$(info doing [$@])
	$(Q)git clean -qffxd

############
# patterns #
############
$(PYTHON_LINT): out/%.lint: %.py .pylintrc
	$(info doing [$@])
	$(Q)pymakehelper error_on_print python -m pylint --reports=n --score=n $<
	$(Q)pymakehelper touch_mkdir $@
$(BASH_CHECK): out/%.check: %.sh .shellcheckrc
	$(info doing [$@])
	$(Q)shellcheck --severity=error --shell=bash --external-sources --source-path="$$HOME" $<
	$(Q)pymakehelper touch_mkdir $@

##########
# alldep #
##########
ifeq ($(DO_ALLDEP),1)
.EXTRA_PREREQS+=$(foreach mk, ${MAKEFILE_LIST},$(abspath ${mk}))
endif # DO_ALLDEP
