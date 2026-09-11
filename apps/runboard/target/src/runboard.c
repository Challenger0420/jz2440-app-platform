#define _GNU_SOURCE

#include <fcntl.h>
#include <linux/fb.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#include "runboard_layout.h"
#include "font_data.h"

#define RB_FRAME_MAX 511
#define RB_TEXT_MAX 48

struct framebuffer { int fd; unsigned char *memory; size_t mapped_length; struct fb_var_screeninfo var; struct fb_fix_screeninfo fix; };
struct experiment_state { char name[RB_TEXT_MAX]; char user[RB_TEXT_MAX]; char status[16]; int progress; int current_round; int total_round; };
struct local_state {
    char server[RB_TEXT_MAX]; char server_status[16]; char server_freshness[12]; char codex_freshness[12];
    int cpu; int ram; int gpu; int experiment_count; struct experiment_state experiment[2];
    int five_hour; int week; int reset_cards; uint32_t sequence; int valid;
};

static uint32_t pack_component(uint8_t value, const struct fb_bitfield *field) { uint32_t max; if (!field->length) return 0; max=(1U<<field->length)-1U; return (((uint32_t)value*max+127U)/255U)<<field->offset; }
static uint32_t pack_rgb(const struct fb_var_screeninfo *var, uint8_t r, uint8_t g, uint8_t b) { return pack_component(r,&var->red)|pack_component(g,&var->green)|pack_component(b,&var->blue); }
static int fb_open(struct framebuffer *fb) { fb->fd=open("/dev/fb0",O_RDWR); if(fb->fd<0)return -1; if(ioctl(fb->fd,FBIOGET_VSCREENINFO,&fb->var)<0||ioctl(fb->fd,FBIOGET_FSCREENINFO,&fb->fix)<0||fb->var.bits_per_pixel!=16){close(fb->fd);return -1;} fb->mapped_length=fb->fix.smem_len; fb->memory=mmap(NULL,fb->mapped_length,PROT_READ|PROT_WRITE,MAP_SHARED,fb->fd,0); if(fb->memory==MAP_FAILED){close(fb->fd);return -1;} return 0; }
static void fb_close(struct framebuffer *fb) { if(fb->memory!=MAP_FAILED)munmap(fb->memory,fb->mapped_length); if(fb->fd>=0)close(fb->fd); }
static void pixel(struct framebuffer *fb,int x,int y,uint32_t color) { uint16_t p; unsigned char *a; if(x<0||y<0||(uint32_t)x>=fb->var.xres||(uint32_t)y>=fb->var.yres)return; a=fb->memory+(size_t)y*fb->fix.line_length+(size_t)x*2U;p=(uint16_t)color;memcpy(a,&p,2); }
static void rect(struct framebuffer *fb,int x,int y,int w,int h,uint32_t c) { int xx,yy; for(yy=y;yy<y+h;yy++)for(xx=x;xx<x+w;xx++)pixel(fb,xx,yy,c); }
static void line_rect(struct framebuffer *fb,int x,int y,int w,int h,uint32_t c) { int i; for(i=x;i<x+w;i++){pixel(fb,i,y,c);pixel(fb,i,y+h-1,c);} for(i=y;i<y+h;i++){pixel(fb,x,i,c);pixel(fb,x+w-1,i,c);} }
static const struct font_glyph *find_glyph(const struct font_face *font,char c){uint8_t i;for(i=0;i<font->count;i++)if(font->glyphs[i].code==(uint8_t)c)return &font->glyphs[i];return NULL;}
static int text_width(const struct font_face *font,const char *s,int spacing){int w=0,n=0;while(*s){const struct font_glyph *g=find_glyph(font,*s++);if(g)w+=g->advance;n++;}return n>1?w+(n-1)*spacing:w;}
static void draw_text(struct framebuffer *fb,int x,int y,const struct font_face *font,const char *s,int spacing,uint32_t color){while(*s){const struct font_glyph *g=find_glyph(font,*s++);if(g){int r,c,stride=(g->width+7)/8;for(r=0;r<g->height;r++)for(c=0;c<g->width;c++){uint8_t a=g->format==FONT_BITMAP_ALPHA?g->data[r*g->width+c]:(g->data[r*stride+c/8]&(uint8_t)(0x80U>>(c%8))?255:0);if(a)pixel(fb,x+c,y+r,color);}x+=g->advance+spacing;}}}
static void copy_text(char *out,const char *in){while((*out++=*in++)!='\0'){} }
static int text_length(const char *text){int n=0;while(text[n])n++;return n;}
static int same_text(const char *a,const char *b){while(*a&&*a==*b){a++;b++;}return *a=='\0'&&*b=='\0';}
static const char *find_text(const char *haystack,const char *needle){int n=text_length(needle),i; if(!n)return haystack; for(;*haystack;haystack++){for(i=0;i<n&&haystack[i]==needle[i];i++){}if(i==n)return haystack;}return NULL;}
static void key_text(char *out,int index,char suffix){out[0]='E';out[1]=(char)('0'+index);out[2]=suffix;out[3]='\0';}
static void append_text(char *out,const char *in,int max){int n=text_length(out),i=0;while(in[i]&&n<max-1)out[n++]=in[i++];out[n]='\0';}
static void number_text(char *out,int value){char r[16];int n=0,i,negative=value<0;if(negative)value=-value;do{r[n++]=(char)('0'+value%10);value/=10;}while(value);if(negative)*out++='-';for(i=0;i<n;i++)out[i]=r[n-i-1];out[n]='\0';}
static int hex_value(char c){if(c>='0'&&c<='9')return c-'0';if(c>='A'&&c<='F')return c-'A'+10;if(c>='a'&&c<='f')return c-'a'+10;return -1;}
static void decode_text(char *out,int max,const char *in){int n=0;while(*in&&n<max-1){if(in[0]=='%'&&hex_value(in[1])>=0&&hex_value(in[2])>=0){out[n++]=(char)(hex_value(in[1])*16+hex_value(in[2]));in+=3;}else{out[n++]=*in++;}}out[n]='\0';}
static uint16_t crc16(const unsigned char *data,int length){uint16_t crc=0xFFFF;int i;while(length--){crc^=(uint16_t)*data++<<8;for(i=0;i<8;i++)crc=(uint16_t)((crc&0x8000)?(crc<<1)^0x1021:crc<<1);}return crc;}
static int parse_int(const char *s,int n,int *out){int i=0,neg=0,v=0;if(!n)return 0;if(s[0]=='-'){neg=1;i++;}if(i==n)return 0;for(;i<n;i++){if(s[i]<'0'||s[i]>'9')return 0;v=v*10+s[i]-'0';if(v>1000000000)return 0;}*out=neg?-v:v;return 1;}
static int field(const char *payload,int length,const char *key,char *value,int max){int p=0,klen=text_length(key);while(p<length){int start=p,end,eq,i;while(p<length&&payload[p]!='|')p++;end=p;eq=start;while(eq<end&&payload[eq]!='=')eq++;if(eq-start==klen){for(i=0;i<klen;i++)if(payload[start+i]!=key[i])break;if(i==klen){int n=end-eq-1;if(n<=0||n>=max)return 0;memcpy(value,payload+eq+1,n);value[n]='\0';return 1;}}if(p<length)p++;}return 0;}
static int integer_field(const char *payload,int length,const char *key,int *value){char text[24];return field(payload,length,key,text,sizeof(text))&&!same_text(text,"?")&&parse_int(text,text_length(text),value);}
static int parse_rb1(const char *frame,int length,struct local_state *state){
    int i,pipe=0,payload_length=-1,crc_position=-1,provided,computed,count,sequence;
    char payload[RB_FRAME_MAX+1],v[RB_TEXT_MAX];
    struct local_state parsed=*state;
    if(length<12||frame[0]!='R'||frame[1]!='B'||frame[2]!='1'||frame[3]!='|'||frame[length-1]!='\n')return 0;
    for(i=0;i<length;i++) if(frame[i]=='|') pipe++;
    if(pipe<4)return 0;
    {const char *length_start=frame+5;int length_count=0;while(length_start[length_count]>='0'&&length_start[length_count]<='9')length_count++;if(length_count<1||length_count>3||!parse_int(length_start,length_count,&payload_length)||payload_length<1||payload_length>RB_FRAME_MAX)return 0;}
    for(i=length-6;i>=4;i--) {
        if(frame[i]=='|'&&frame[i+1]=='C'&&frame[i+2]=='R'&&frame[i+3]=='C'&&frame[i+4]=='=') { crc_position=i; break; }
    }
    if(crc_position<0)return 0;
    {const char *payload_start=find_text(frame,"|V="); if(!payload_start)return 0; payload_start+=1; if(frame+crc_position-payload_start!=payload_length)return 0; memcpy(payload,payload_start,(size_t)payload_length);payload[payload_length]='\0';}
    {int digit;provided=0;for(i=crc_position+5;i<length-1;i++){digit=hex_value(frame[i]);if(digit<0)return 0;provided=(provided<<4)|digit;}if(i-(crc_position+5)!=4)return 0;}
    computed=crc16((unsigned char*)payload,payload_length);if((provided&0xFFFF)!=computed)return 0;
    if(!field(payload,payload_length,"V",v,sizeof(v))||!same_text(v,"1"))return 0;
    parsed.valid=1; sequence=0; if(integer_field(payload,payload_length,"SEQ",&sequence)) parsed.sequence=(uint32_t)sequence;
    if(field(payload,payload_length,"SD",v,sizeof(v)))decode_text(parsed.server,sizeof(parsed.server),v);else copy_text(parsed.server,"SERVER-01");
    if(field(payload,payload_length,"SS",v,sizeof(v)))decode_text(parsed.server_status,sizeof(parsed.server_status),v);else copy_text(parsed.server_status,"offline");
    if(field(payload,payload_length,"SF",v,sizeof(v)))decode_text(parsed.server_freshness,sizeof(parsed.server_freshness),v);else copy_text(parsed.server_freshness,"fresh");
    if(field(payload,payload_length,"CF",v,sizeof(v)))decode_text(parsed.codex_freshness,sizeof(parsed.codex_freshness),v);else copy_text(parsed.codex_freshness,"fresh");
    parsed.cpu=parsed.ram=parsed.gpu=-1;integer_field(payload,payload_length,"CPU",&parsed.cpu);integer_field(payload,payload_length,"RP",&parsed.ram);integer_field(payload,payload_length,"GU",&parsed.gpu);
    count=0;if(integer_field(payload,payload_length,"EC",&count)&&count>=0&&count<=2)parsed.experiment_count=count;else parsed.experiment_count=0;
    for(i=0;i<2;i++){struct experiment_state *e=&parsed.experiment[i];char key[8];copy_text(e->name,"unknown");copy_text(e->user,"unknown");copy_text(e->status,"UNKNOWN");e->progress=e->current_round=e->total_round=-1; if(i>=parsed.experiment_count)continue; key_text(key,i,'N');if(field(payload,payload_length,key,v,sizeof(v)))decode_text(e->name,sizeof(e->name),v);key_text(key,i,'U');if(field(payload,payload_length,key,v,sizeof(v)))decode_text(e->user,sizeof(e->user),v);key_text(key,i,'S');if(field(payload,payload_length,key,v,sizeof(v)))decode_text(e->status,sizeof(e->status),v);key_text(key,i,'P');integer_field(payload,payload_length,key,&e->progress);key_text(key,i,'R');integer_field(payload,payload_length,key,&e->current_round);key_text(key,i,'T');integer_field(payload,payload_length,key,&e->total_round);}
    parsed.five_hour=parsed.week=parsed.reset_cards=-1;integer_field(payload,payload_length,"C5",&parsed.five_hour);integer_field(payload,payload_length,"CW",&parsed.week);integer_field(payload,payload_length,"CRC",&parsed.reset_cards);*state=parsed;return 1;
}
static void draw_screen(struct framebuffer *fb,const struct local_state *state){uint32_t bg=pack_rgb(&fb->var,RB_COLOR_BG_R,RB_COLOR_BG_G,RB_COLOR_BG_B),text=pack_rgb(&fb->var,RB_COLOR_TEXT_R,RB_COLOR_TEXT_G,RB_COLOR_TEXT_B),muted=pack_rgb(&fb->var,RB_COLOR_MUTED_R,RB_COLOR_MUTED_G,RB_COLOR_MUTED_B),green=pack_rgb(&fb->var,RB_COLOR_GREEN_R,RB_COLOR_GREEN_G,RB_COLOR_GREEN_B),yellow=pack_rgb(&fb->var,RB_COLOR_YELLOW_R,RB_COLOR_YELLOW_G,RB_COLOR_YELLOW_B),red=pack_rgb(&fb->var,RB_COLOR_RED_R,RB_COLOR_RED_G,RB_COLOR_RED_B),border=pack_rgb(&fb->var,RB_COLOR_BORDER_R,RB_COLOR_BORDER_G,RB_COLOR_BORDER_B);char line[96],num[16];int w,i;rect(fb,0,0,RUNBOARD_SCREEN_W,RUNBOARD_SCREEN_H,bg);draw_text(fb,14,7,&font_header,"RUNBOARD",1,text);draw_text(fb,155,20,&font_label,state->server,1,muted);draw_text(fb,400,18,&font_label,state->server_status,1,state->server_status[0]=='o'?green:yellow);copy_text(line,"CPU ");if(state->cpu<0)append_text(line,"--",sizeof(line));else{number_text(num,state->cpu);append_text(line,num,sizeof(line));}append_text(line,"%  RAM ",sizeof(line));if(state->ram<0)append_text(line,"--",sizeof(line));else{number_text(num,state->ram);append_text(line,num,sizeof(line));}append_text(line,"%  GPU ",sizeof(line));if(state->gpu<0)append_text(line,"--",sizeof(line));else{number_text(num,state->gpu);append_text(line,num,sizeof(line));}append_text(line,"%",sizeof(line));draw_text(fb,155,34,&font_label,line,1,muted);if(!state->valid){draw_text(fb,190,115,&font_large_number,"OFFLINE",1,red);return;}if(state->experiment_count==0){draw_text(fb,190,112,&font_large_number,"IDLE",2,green);}else{for(i=0;i<state->experiment_count;i++){int px=state->experiment_count==1?14:(i==0?14:242);int pw=state->experiment_count==1?452:224;int py=state->experiment_count==1?52:52;int ph=state->experiment_count==1?134:134;if(i>0)py=193;ph=i>0?43:134;line_rect(fb,px,py,pw,ph,border);draw_text(fb,px+10,py+8,&font_label,i==0?"CURRENT EXPERIMENT":"SECOND EXPERIMENT",1,muted);draw_text(fb,px+10,py+27,&font_label,state->experiment[i].name,0,text);draw_text(fb,px+10,py+48,&font_label,state->experiment[i].user,1,muted);draw_text(fb,px+10,py+70,&font_label,state->experiment[i].status,1,state->experiment[i].status[0]=='R'?green:(state->experiment[i].status[0]=='E'?red:yellow));if(state->experiment[i].progress>=0){number_text(num,state->experiment[i].progress);copy_text(line,num);append_text(line,"%",sizeof(line));draw_text(fb,px+10,py+(i==0?94:25),&font_large_number,line,1,text);}}}rect(fb,0,RUNBOARD_FOOTER_Y,RUNBOARD_SCREEN_W,32,pack_rgb(&fb->var,RB_COLOR_PANEL_R,RB_COLOR_PANEL_G,RB_COLOR_PANEL_B));if(state->experiment_count==1){copy_text(line,"CODEX 5H ");number_text(num,state->five_hour);append_text(line,num,sizeof(line));append_text(line,"% W ",sizeof(line));number_text(num,state->week);append_text(line,num,sizeof(line));append_text(line,"% RC ",sizeof(line));number_text(num,state->reset_cards);append_text(line,num,sizeof(line));draw_text(fb,14,RUNBOARD_FOOTER_Y+8,&font_label,line,1,text);}else if(state->experiment_count>=2)draw_text(fb,14,RUNBOARD_FOOTER_Y+8,&font_label,"SECOND EXPERIMENT",1,text);copy_text(line,state->server_freshness);append_text(line,"/",sizeof(line));append_text(line,state->codex_freshness,sizeof(line));w=text_width(&font_label,line,1);draw_text(fb,RUNBOARD_SCREEN_W-w-12,RUNBOARD_FOOTER_Y+8,&font_label,line,1,muted);}
static int serial_open(const char *path,struct termios *saved)
{
    struct termios t;
    int fd=open(path,O_RDWR|O_NOCTTY|O_NONBLOCK);
    if(fd<0)return -1;
    if(ioctl(fd,TCGETS,&t)<0){close(fd);return -1;}
    *saved=t;
    t.c_iflag&=~(IGNBRK|BRKINT|PARMRK|ISTRIP|INLCR|IGNCR|ICRNL|IXON|IXOFF|IXANY);
    t.c_oflag&=~OPOST;
    t.c_lflag&=~(ECHO|ECHONL|ICANON|ISIG|IEXTEN);
    t.c_cflag&=~(CSIZE|PARENB|PARODD|CSTOPB);
#ifdef CRTSCTS
    t.c_cflag&=~CRTSCTS;
#endif
    t.c_cflag|=CS8|CLOCAL|CREAD;
    t.c_ispeed=B115200;
    t.c_ospeed=B115200;
    t.c_cc[VMIN]=0;
    t.c_cc[VTIME]=0;
    if(ioctl(fd,TCSETS,&t)<0){close(fd);return -1;}
    return fd;
}
static void serial_close(int fd,const struct termios *saved){if(fd<0)return;ioctl(fd,TCSETS,saved);close(fd);}
static int is_quit(const char *frame){return same_text(frame,"<RBQUIT>");}
int main(int argc,char **argv){struct framebuffer fb;struct local_state state;struct termios saved_serial;char frame[RB_FRAME_MAX+1];int fd=-1,n=0,overflow=0,i;const char *serial_path="/dev/s3c2410_serial0";fb.fd=-1;fb.memory=MAP_FAILED;fb.mapped_length=0;memset(&fb.var,0,sizeof(fb.var));memset(&fb.fix,0,sizeof(fb.fix));memset(&state,0,sizeof(state));memset(&saved_serial,0,sizeof(saved_serial));state.cpu=state.ram=state.gpu=state.five_hour=state.week=state.reset_cards=-1;copy_text(state.server,"SERVER-01");copy_text(state.server_status,"offline");if(argc==3&&same_text(argv[1],"--serial"))serial_path=argv[2];else if(argc!=1){return 2;}if(fb_open(&fb)<0)return 1;fd=serial_open(serial_path,&saved_serial);if(fd<0){fb_close(&fb);return 1;}draw_screen(&fb,&state);for(;;){unsigned char bytes[96];struct timespec pause={1,0};int count=(int)read(fd,bytes,sizeof(bytes));if(count>0)for(i=0;i<count;i++){if(bytes[i]=='\n'){if(!overflow&&n>0){if(frame[n-1]=='\r')n--;frame[n]='\0';if(is_quit(frame)){serial_close(fd,&saved_serial);fb_close(&fb);return 0;}if(find_text(frame,"RB1|")==frame&&parse_rb1(frame,n+1,&state))draw_screen(&fb,&state);}n=0;overflow=0;}else if(n<RB_FRAME_MAX&&!overflow)frame[n++]=(char)bytes[i];else overflow=1;}nanosleep(&pause,NULL);} }
