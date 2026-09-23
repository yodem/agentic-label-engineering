def lum(h):
    h=h.lstrip('#'); c=[int(h[i:i+2],16)/255 for i in (0,2,4)]
    c=[x/12.92 if x<=0.03928 else ((x+0.055)/1.055)**2.4 for x in c]
    return 0.2126*c[0]+0.7152*c[1]+0.0722*c[2]
def cr(a,b):
    la,lb=lum(a),lum(b); return (max(la,lb)+0.05)/(min(la,lb)+0.05)
T={
'light':dict(bg='#F1F3F5',surface='#FFFFFF',text='#17202B',text2='#44505E',text3='#5E6977',borderstrong='#5E6977',
 needs='#B45309',needs_t='#FEF3C7',fail='#B91C1C',fail_t='#FEE2E2',run='#1D4ED8',run_t='#DBEAFE',verify='#6D28D9',verify_t='#EDE9FE',
 ready='#0F766E',ready_t='#CCFBF1',wait='#44505E',wait_t='#E9EDF1',done='#15803D',done_t='#DCFCE7',cancel='#5E6977',cancel_t='#E9EDF1',selected='#E6EEFB',focus='#FDE68A'),
'dark':dict(bg='#11161C',surface='#19202A',text='#EDF1F5',text2='#C3CCD6',text3='#93A0AE',borderstrong='#93A0AE',
 needs='#FBBF24',needs_t='#3A2A0A',fail='#F87171',fail_t='#3F1414',run='#60A5FA',run_t='#152542',verify='#A78BFA',verify_t='#2A1C4F',
 ready='#2DD4BF',ready_t='#0B2E2B',wait='#C3CCD6',wait_t='#222B36',done='#4ADE80',done_t='#0F2E1C',cancel='#93A0AE',cancel_t='#222B36',selected='#1D2F4F',focus='#FDE68A'),
}
import sys
bad=0
for m,t in T.items():
    print('==',m)
    for k in ['text','text2','text3','borderstrong']:
        print(f"{k:12} bg {cr(t[k],t['bg']):.2f} surf {cr(t[k],t['surface']):.2f} sel {cr(t[k],t['selected']):.2f}")
    for s in ['needs','fail','run','verify','ready','wait','done','cancel']:
        tint=t[s+'_t']
        r=(cr(t[s],t['bg']),cr(t[s],t['surface']),cr(t[s],tint),cr(t['text'],tint),cr(t['text2'],tint),cr(t['text3'],tint))
        print(f"{s:7} {t[s]} bg {r[0]:.2f} surf {r[1]:.2f} own-tint {r[2]:.2f} | on tint: text {r[3]:.2f} text2 {r[4]:.2f} text3 {r[5]:.2f}")
        if min(r[:4])<4.5 or r[4]<4.5: bad+=1; print('   FAIL')
    print('focus text', cr('#17202B' if m=='light' else '#11161C', t['focus']))
print('bad',bad)
