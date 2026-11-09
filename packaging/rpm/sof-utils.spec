# SPDX-License-Identifier: BSD-3-Clause
%global debug_package %{nil}
%global _build_id_links none

%{!?pkg_version: %define pkg_version 0.1.0}
%{!?pkg_release: %define pkg_release 1}

Name:           sof-utils
Version:        %{pkg_version}
Release:        %{pkg_release}%{?dist}
Summary:        Sound Open Firmware (SOF) Utilities, GTK4 Visual Tools, Remote Server & Testbench
License:        BSD-3-Clause AND (GPL-2.0-only OR BSD-3-Clause)
URL:            https://github.com/thesofproject/sof-utils
Source0:        %{name}-%{version}.tar.gz

BuildRequires:  gcc
BuildRequires:  make
BuildRequires:  cmake
BuildRequires:  python3
Requires:       python3
Requires:       alsa-utils

%description
Developer utilities, visual GTK4 applications, and host/DUT tools for the
Sound Open Firmware (SOF) ecosystem:
- sof-builder: GTK4 Topology2 Visual Graph Editor, IPC4 Compiler & Testbench Studio
- sof-top: GTK4 Live DSP Resource, Memory (SRAM + DRAM) & Task Monitor
- sof-remote-server & sof-remote: TCP Telemetry & Control Daemon and CLI for DUTs
- sof-testbench4 & sof-tb-ctl: Multi-core LL/DP Host DSP Simulator & Control CLI
- sof-tplg-parser: ALSA Topology (.tplg) IPC3/IPC4 Parser Library

%prep
%setup -q -n %{name}-%{version}

%build
%{__make} PREFIX=%{_prefix} LIBDIR=%{_libdir} INCLUDEDIR=%{_includedir} \
    %{?_sof_src_dir:SOF_SRC_DIR="%{_sof_src_dir}"} \
    %{?_work_dir:WORK_DIR="%{_work_dir}"} \
    all

%install
rm -rf %{buildroot}
%{__make} DESTDIR=%{buildroot} PREFIX=%{_prefix} LIBDIR=%{_libdir} INCLUDEDIR=%{_includedir} \
    %{?_sof_src_dir:SOF_SRC_DIR="%{_sof_src_dir}"} \
    %{?_work_dir:WORK_DIR="%{_work_dir}"} \
    install

%files
%license LICENSE
%{_bindir}/sof-builder
%{_bindir}/sof-top
%{_bindir}/sof_remote_server
%{_bindir}/sof-remote-server
%{_bindir}/sof-remote
%{_datadir}/sof-utils/
%{_datadir}/applications/org.thesofproject.SofBuilder.desktop
%{_datadir}/applications/org.thesofproject.SofTop.desktop
%{_datadir}/doc/sof-utils/
%if 0%{?with_testbench}
%{_bindir}/sof-testbench*
%{_bindir}/sof-tb-ctl
%{_libdir}/libsof_tplg_parser.a
%{_includedir}/tplg_parser/
%endif

%changelog
* Mon Nov 09 2026 Liam Girdwood <liam.r.girdwood@intel.com> - 0.1.0-1
- Initial package for sof-utils (sof-builder, sof-top, sof-remote-server, sof-testbench, sof-tplg-parser)
