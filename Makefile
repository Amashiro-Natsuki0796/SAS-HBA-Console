PREFIX ?= /usr
DESTDIR ?=
PYTHON ?= python3
LIBDIR = $(DESTDIR)$(PREFIX)/lib/python3/dist-packages
APPDIR = $(DESTDIR)$(PREFIX)/share/sas-hba-console
ICONDIR = $(DESTDIR)$(PREFIX)/share/icons/hicolor

.PHONY: all check test install uninstall clean deb

all:
	$(PYTHON) -m compileall -q sas_hba_console

check: test

test:
	$(PYTHON) -m unittest discover -s tests -v

install:
	install -d $(LIBDIR)/sas_hba_console
	cp -a sas_hba_console/. $(LIBDIR)/sas_hba_console/
	find $(LIBDIR)/sas_hba_console -name '__pycache__' -type d -prune -exec rm -rf {} +
	find $(LIBDIR)/sas_hba_console -name '*.pyc' -delete
	install -d $(APPDIR)
	cp -a assets/. $(APPDIR)/
	install -d $(DESTDIR)$(PREFIX)/bin
	install -m 0755 packaging/sas-hba-console $(DESTDIR)$(PREFIX)/bin/sas-hba-console
	ln -sf sas-hba-console $(DESTDIR)$(PREFIX)/bin/lsi9207-console
	install -d $(DESTDIR)$(PREFIX)/libexec
	install -m 0755 packaging/sas-hba-helper $(DESTDIR)$(PREFIX)/libexec/sas-hba-helper
	install -d $(DESTDIR)$(PREFIX)/share/applications
	install -m 0644 packaging/sas-hba-console.desktop $(DESTDIR)$(PREFIX)/share/applications/
	install -d $(DESTDIR)$(PREFIX)/share/polkit-1/actions
	install -m 0644 packaging/io.github.sashbaconsole.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/
	install -d $(DESTDIR)$(PREFIX)/share/metainfo
	install -m 0644 packaging/io.github.sashbaconsole.metainfo.xml $(DESTDIR)$(PREFIX)/share/metainfo/
	install -d $(DESTDIR)$(PREFIX)/share/doc/sas-hba-console
	install -m 0644 README.md $(DESTDIR)$(PREFIX)/share/doc/sas-hba-console/README.md
	install -m 0644 docs/README-hardware.md $(DESTDIR)$(PREFIX)/share/doc/sas-hba-console/README-hardware.md 2>/dev/null || true
	install -d $(DESTDIR)$(PREFIX)/share/man/man1
	install -m 0644 docs/sas-hba-console.1 $(DESTDIR)$(PREFIX)/share/man/man1/
	ln -sf sas-hba-console.1 $(DESTDIR)$(PREFIX)/share/man/man1/lsi9207-console.1
	for size in 48 64 128 256; do \
		install -d $(ICONDIR)/$${size}x$${size}/apps; \
		install -m 0644 assets/icons/$${size}x$${size}/apps/sas-hba-console.png \
			$(ICONDIR)/$${size}x$${size}/apps/sas-hba-console.png; \
	done
	install -d $(ICONDIR)/scalable/apps
	install -m 0644 assets/icons/scalable/apps/sas-hba-console.svg \
		$(ICONDIR)/scalable/apps/sas-hba-console.svg
	if [ -z "$(DESTDIR)" ]; then \
		gtk-update-icon-cache -q -t -f $(PREFIX)/share/icons/hicolor 2>/dev/null || true; \
		update-desktop-database -q $(PREFIX)/share/applications 2>/dev/null || true; \
	fi

uninstall:
	rm -rf $(LIBDIR)/sas_hba_console $(APPDIR)
	rm -f $(DESTDIR)$(PREFIX)/bin/sas-hba-console $(DESTDIR)$(PREFIX)/bin/lsi9207-console
	rm -f $(DESTDIR)$(PREFIX)/libexec/sas-hba-helper
	rm -f $(DESTDIR)$(PREFIX)/share/applications/sas-hba-console.desktop
	rm -f $(DESTDIR)$(PREFIX)/share/metainfo/io.github.sashbaconsole.metainfo.xml
	rm -f $(DESTDIR)$(PREFIX)/share/polkit-1/actions/io.github.sashbaconsole.policy
	rm -rf $(DESTDIR)$(PREFIX)/share/doc/sas-hba-console
	rm -f $(DESTDIR)$(PREFIX)/share/man/man1/sas-hba-console.1 $(DESTDIR)$(PREFIX)/share/man/man1/lsi9207-console.1

deb:
	dpkg-buildpackage -us -uc -b
	mkdir -p dist
	mv ../sas-hba-console_*.deb dist/ 2>/dev/null || true

clean:
	rm -rf debian/sas-hba-console debian/.debhelper debian/files *.deb dist
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
