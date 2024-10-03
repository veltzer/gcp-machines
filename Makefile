##############
# parameters #
##############
# do you want to see the commands executed ?
DO_MKDBG:=0
# do you want to check the javascript code?
DO_CHECKJS:=1
# do you want to validate html?
DO_CHECKHTML:=1
# do you want to validate css?
DO_CHECKCSS:=0
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

JSCHECK:=out/jscheck.stamp
HTMLCHECK:=out/html.stamp
CSSCHECK:=out/css.stamp

CLEAN:=
ALL:=

PYTHON_SRC:=$(shell find scripts -type f -and -name "*.py")
PYTHON_LINT=$(addprefix out/, $(addsuffix .lint, $(basename $(PYTHON_SRC))))

BASH_SRC:=$(shell find scripts -type f -and -name "*.sh")
BASH_CHECK:=$(addprefix out/, $(addsuffix .check, $(basename $(BASH_SRC))))

SOURCES_JS:=$(shell find static/js -type f -and -name "*.js")
SOURCES_HTML:=$(shell find static/html -type f -and -name "*.html")
SOURCES_CSS:=$(shell find static/css -type f -and -name "*.css")

ifeq ($(DO_CHECKJS),1)
ALL+=$(JSCHECK)
all: $(ALL)
CLEAN+=$(JSCHECK)
endif # DO_CHECKJS

ifeq ($(DO_CHECKHTML),1)
ALL+=$(HTMLCHECK)
CLEAN+=$(HTMLCHECK)
endif # DO_CHECKHTML

ifeq ($(DO_CHECKCSS),1)
ALL+=$(CSSCHECK)
CLEAN+=$(CSSCHECK)
endif # DO_CHECKCSS

ifeq ($(DO_PYLINT),1)
ALL+=$(PYTHON_LINT)
CLEAN+=$(PYTHON_LINT)
endif # DO_PYLINT

ifeq ($(DO_BASH_CHECK),1)
ALL+=$(BASH_CHECK)
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
.PHONY: clean
clean:
	$(info doing [$@])
	$(Q)-rm -f $(CLEAN)
.PHONY: clean_hard
clean_hard:
	$(info doing [$@])
	$(Q)git clean -qffxd
.PHONY: checkjs
checkjs: $(JSCHECK)
	$(info doing [$@])
.PHONY: checkhtml
checkhtml: $(HTMLCHECK)
	$(info doing [$@])
.PHONY: checkcss
checkcss: $(CSSCHECK)
	$(info doing [$@])
$(JSCHECK): $(SOURCES_JS)
	$(info doing [$@])
	$(Q)pymakehelper touch_mkdir $@
# $(Q)pymakehelper only_print_on_error $(TOOL_GJSLINT) --flagfile support/gjslint.cfg $(SOURCES_JS)
# $(Q)$(TOOL_JSL) --conf=support/jsl.conf --quiet --nologo --nosummary --nofilelisting $(SOURCES_JS)
$(HTMLCHECK): $(SOURCES_HTML)
	$(info doing [$@])
	$(Q)pymakehelper touch_mkdir $@
#$(Q)pymakehelper only_print_on_error node_modules/.bin/htmlhint $(SOURCES_HTML)
#$(Q)$(TOOL_TIDY) -errors -q -utf8 $(SOURCES_HTML)
$(CSSCHECK): $(SOURCES_CSS)
	$(info doing [$@])
	$(Q)pymakehelper wrapper_css_validator java -jar $(TOOL_CSS_VALIDATOR) --profile=css3 --output=text -vextwarning=true --warning=0 $(addprefix file:,$(SOURCES_CSS))
	$(Q)pymakehelper touch_mkdir $@

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
