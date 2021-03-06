# This file contains the functions used to process the preprocessed 
# input images into tabular form
import numpy as np 
import pdf2image
import pandas as pd
import os
import sys
import io
import logging
import re
import layoutparser as lp
from PIL import Image
from pyparsing import col
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("Processing")
import yaml
with open('config.yml') as f:
    try:
        cfg = yaml.load(f, Loader=yaml.SafeLoader)
        print(dict)
    except yaml.YAMLError as e:
        print(e)
import ocr as o
import det2 as d2

def convert_PDF(file, pagenum):
    """
    Converts pdf to numpy array
        file: The file path of the PDF
        pagenum: the page number of the PDF to convert
    """
    pdf = np.asarray(pdf2image.convert_from_path(file, dpi=250, first_page=pagenum, last_page=pagenum)[0])
    log.info("Converted page {} from {}".format(pagenum, file))   
    return pdf
        

def to_pos_id(y_1 = float, y_2 = float, pagenum = int, docheight = 3850) -> float:
    """
    Returns a position ID (float) of its position on the page as a decimal from 0 to 1, added on top of the pagenum

    Ex: A page that is 500px tall, with y_1 = 225 and y_2 = 275 on page 5. 
    Average height on page is 250px, so returns .5. Added to the page number gives the Position ID
    of 5.5. This is used to have a scalar value for the absolute position of objects in an entire document, instead of just simple pages. 
    Intended to be used to match up titles and tables that get cut off/on different pages from each other. 

    Arguments: 
        y_1: Lower Y value of the selection 
        y_2: Higher Y value of the selection 
        pagenum: Page number of the page where the selection came from 
        docheight: Height, in pixels, of the array of the document. Defaults to 3850, as that is dpi = 350 of the PDF
    """
    pos_id = float(pagenum) + (((y_1 + y_2)/2)/docheight)
    log.info("Created Position ID {} for page {}".format(pos_id, pagenum))
    return pos_id
   
           
def layout_excluding_layout(layout, filter_layout):
    """
    This function takes a Layout variable and removes all units that fall inside another Layout file
    Arguments: 
        layout: The source layout. In this case, the text layout. 
        filter_layout: The layout that the filter checks against. Everything from layout that lies within a unit of filter_layout will be removed by this function. 
        padding: automatic function to add padding 
    """
    x = lp.Layout([b for b in layout \
        if not any(b.is_in(b_tab) for b_tab in filter_layout)])
    log.info("excluded filter_layout from the layout")
    return x


def text_layout_from_selection(text_layer, bounding_layer):
    """Returns text from selected boundary layer"""
    return text_layer.filter_by(bounding_layer)

def list_text_layout_from_selection(text_layer, bounding_layers):
    """Inputs a text layer a Layer of several bounding layers. It returns the text from each individual bounding polygon using text_from_selection() and returns them in list form."""
    l = []
    for i,x in enumerate(bounding_layers): 
        z = text_layout_from_selection(text_layer, x)
        log.info('Converted bounding layer {}'.format(i))
    return z

def remove_titles(bounding_layers, cfgtable = cfg['Table']):
    px = cfgtable['titlerow_px'] + cfgtable['Padding']['top']
    return bounding_layers.pad(top=-px)

def remove_many_titles(table_poly):
    l = []
    for i in table_poly:
        _ = remove_titles(i)
        l.append(_)
    return l

 
def isolate_titles(bounding_poly, cfgtable = cfg['Table']):
    """Takes padded bounding polygon of table, removes padding, and then removes all put the pixels set in titlerow_px to isolate the title row"""
    no_toptitle = bounding_poly.pad(top = -cfgtable['Padding']['top'])
    height = no_toptitle.height
    px = height - cfgtable['titlerow_px'] 
    isolated = no_toptitle.pad(bottom=-px)
    log.info('isolated title')
    return isolated

def to_polygons(bounding_layer):
    """Converts Layer object into a simpler polygon"""
    return bounding_layer.get_homogeneous_blocks()

def cols_px(bounding, cfgtable = cfg['Table']):
    """
    uses settings in config.yml to determine the location of each column based on the location of the column titles. 
    Still a bit of WIP, changes in config.yml will change a lot too, as doing a lot of bug testing atm.  
    
    """
    df = bounding.to_dataframe() # Turns into dataframe
    try:
        df['x_1'] = [i[0] for i in df['points']] # separates coordinates 
        df['x_2'] = [i[2] for i in df['points']]
        df['x_avg'] = (df['x_1'] + df['x_2'])/2
    except: 
        log.error(df)
        log.error(bounding)
    #for u in df: 
    namedf = pd.DataFrame(columns = ['ColNum', 'x_avg', 'texts'])
    for _ in df['text']:
        for x,i in enumerate(cfgtable['columns']): # Iterates over every word and every column in config.yml
            if bool(re.search(str(cfgtable['columns'][i]['regex']), _.lower())):
                df1 = df[df['text'] == _].copy()
                avg_val = df1['x_avg'].values[0] 
                df1['ColNum'] = x
                title_search_dist = cfgtable['columns'][i]['title_search_dist'] # Searches in x distance on either side of identified column, setting in config.yml
                selected = df[abs(df['x_avg'] - avg_val) < title_search_dist].copy() # Creates df with all of the ones identified with above command 
                selected['ColNum'] = x
                texts = 'failed' # Texts for log
                if x not in namedf['ColNum'].values: # If the column number is not already added to the dataframe from previous runs of loop, add it
                    gb = selected.groupby('ColNum', as_index=False).mean() # Group by column number, average x_avg
                    texts = selected['text'].values
                    gb['text'] = str(texts) # Sets text column to the text of the selected column
                    namedf = pd.concat([namedf, gb])
                log.info('Appended column {}, identified by {} with {} distance one each side'.format(x, texts, title_search_dist))

    namedf = namedf[['ColNum', 'x_avg']].sort_values(by='ColNum').reset_index(drop=True)
    print(namedf)
    return namedf

def column_poly(table_text_poly, cols_px_df, gcv_word, cfgtable = cfg['Table']):
    """
    Takes column positions in the form of a dataframe from cols_px() returns polygons for each section. 
        
        Arguments: 
        table_text_poly: The source polygon. if a layerfile, make sure to set [i] on the end to get a specific table. 
        cols_px_df: df given by cols_px
        cfgtable: config file's table section, will work automatically by default
    Other important information: 

        - Each column will have a "special" section. at the moment you should only really have that set on the first column, as I am using it for dynamic sizing for
        the first column only and it is kinda hardcoded at the moment. special gap sets the gap. check the comments on the code of the function for more information. 
    """
    coords = table_text_poly.coordinates # gets coordinates of the bounding layer
    layouts = []
    
    for i,x in enumerate(cfgtable['columns']):
             rec = lp.Rectangle(
                x_1 = (cols_px_df['x_avg'][i] - cfgtable['columns'][x]['hard_margin']['left']),
                y_1 = coords[1],
                x_2 = (cols_px_df['x_avg'][i] + cfgtable['columns'][x]['hard_margin']['right']),
                y_2 = coords[3])
             layouts.append(rec)     
             if cfgtable['columns'][x]['special'] == True: 
                 spl = x
    tb = layouts[0].coordinates # checks coordinates of title_of_bond and stores them 
    ri = layouts[1].coordinates # checks coordinates for the second row, as it will use the second row to dynamically assign the first row
    layouts[0] = lp.Rectangle(
        # This whole section just sets the first row's x_2 to the x_1 of the second row, minus a special_gap
        x_1 = tb[0],
        y_1 = tb[1],
        x_2 = ( ri[0] - cfgtable['columns'][spl]['special_gap']),
        y_2 = tb[3]
    )
    return layouts

def txt_from_col_poly(col_poly, gcv_word, cfgtable = cfg['Table']): 
    """
    Takes the output from identify_rows and puts everything in a nice data frame. 
    """
    cols = []
    for i,x in enumerate(cfgtable['columns']):
        filtered = gcv_word.filter_by(
            col_poly[i], 
            soft_margin = cfgtable['columns'][x]['soft_margin']
        , center = True)
        cols.append(filtered)
    return cols


def identify_rows(col_txt_list, distance_th, gcv_word, tabletitletext, cfgtable = cfg['Table']): 
    """
    used to dynamically calculate rows based on distances between the x values of various things. 
    col_text_list must be a list of polygons defining the various different columns, and must not have a title in it. 
    distance_th is the distance between the cenwter of the polygons 
    gcv_word is google cloud word thing 
    returns list of list, first subsetting is by col second is by row. 
    """
    list = []
    #df = pd.DataFrame()
    blocks = txt_from_col_poly(col_txt_list, gcv_word, cfgtable)
    o = 0
    
    for i in blocks: 
        i = sorted(i, key = lambda x: x.coordinates[1]) # Sort the blocks vertically from top to bottom
        distances = np.array([((b2.coordinates[1] + b2.coordinates[3])/2) - ((b1.coordinates[3] + b1.coordinates[1])/2) for (b1, b2) in zip(i, i[1:])])
        # Calculate the distances:
        # y coord for the upper edge of the bottom block -
        #   y coord for the bottom edge of the upper block
        # And convert to np array for easier post processing
        distances = np.append([0], distances) # Append a placeholder for the first word
        block_group = (distances>distance_th).cumsum() # Create a block_group based on the distance threshold
        grouped_blocks = [lp.Layout([]) for i in range(max(block_group)+1)]
        for _, block in zip(block_group, i):
            grouped_blocks[_].append(block)   
    #    df[str(i)] = pd.Series(grouped_blocks) 
        #for i in lp.io.load_csv(io.StringIO(px.to_csv())):
        #    if i in grouped_blocks: grouped_blocks.remove(i) 
        for i in tabletitletext: 
            if i in grouped_blocks: grouped_blocks.remove(i)
        list.append(grouped_blocks)
       
    return list


def layer_to_df(double_layered_list): 
    """
    Converts double layered list from identify_rows into a dataframe. 

    Issues: 
        - If first row has the incorrect number of columns, it will run into issues. Requires more testing 
    """
    count = 0 # simple count used to tell if this is the first row of the dataframe or not. ``
    for p,i in enumerate(double_layered_list):  #Enumerates over the double layered list. p gives integer number and i gives the value 
        col = pd.DataFrame() # Creates another df that only lasts for one loop. Is used to store the y value of each one to aid in matching 
        list = []
        y1list = []
        for u in i: 
            sort = sorted(u, key = lambda x: x.coordinates[0]) # inside each row/col pair, there are sometimes multiple text boxes. 
            text = ' '.join(lp.Layout(sort).get_texts())  # This sorts these text boxes from left to right according to x value, so they combine correctly
            y1 = u[0].coordinates[1] # Gets y_1 coordinate for this entry 
            list.append(text)
            y1list.append(y1)
        col['y_1'] = pd.Series(y1list) # adds coordinates to df
        col[str(p)] = pd.Series(list) # adds corresponding text to each 
        if count == 0: 
            df = col.copy() # If first row, creates dataframe entirely
        if not count == 0:  # If not the first, continues. Below is a bit complicated, basically merges to nearest value
            df1 = pd.merge_asof(left = col, right = df.reset_index(), on='y_1', direction='nearest') 
            # I had split it into two separate things, by reversing the first merge and then merging on the index of the first merge
            # Otherwise, if the right side had less than the original left side, it would just duplicate the nearest one. This makes it unique assignment 
            df = df.merge(df1[['index', str(p)]], how='left', left_index=True,  right_on='index').set_index('index')
        count += 1
    df = df.drop(columns='y_1') # Drops coords as they aren't needed
    return df
  
  
def cont_or_not(table_poly, gcv_word, cfgtable=cfg['Table']):
    title = table_poly.pad(-cfgtable['Padding']['top'])
    titletext = gcv_word.filter_by(
       title, 
       soft_margin = cfgtable['cont']['cont_soft_margin']
    )
    texts = titletext.get_texts()
    text = ' '.join(str(e) for e in texts)
    if bool(re.search(str(cfgtable['cont']['regex']), text.lower())): 
        return True
    else: return False


    
def parse_table(table_layout, gcv_word, tablenum, image, cfgtable = cfg['Table']): 
    """
    combines many functions into one. Essentially all you need is the table layout, gcv_word, 
    and tablenum and it will return you a dataframe of all of the text within each table
    """
    table_poly = to_polygons(table_layout) # Convert to polygon 
    isolated = isolate_titles(table_poly[tablenum], cfgtable)
    tabletitletext = gcv_word.filter_by(
       isolated,
       soft_margin = cfgtable['title_soft_margin'] 
    )
    try: px = cols_px(tabletitletext, cfgtable) # location of each column
    except: 
        im = Image.fromarray(isolated.crop_image(image))
        im.save('Tests/{}_isolated.png'.format(tablenum), 'PNG')
        #im = Image.fromarray(table_poly.crop_image(image))
        #im.save('Tests/{}.png'.format(tablenum), 'PNG')
        log.error(isolated)
    if px.isnull().values.any(): 
            log.error(px)
            return px
    if not len(px['x_avg']) == 6: 
        print(px)
        print(tabletitletext)
        return px
    table_titleless = remove_titles(table_poly[tablenum], cfgtable) # returns poly for specified tablenum but without 
    col_poly = column_poly(table_titleless, px, gcv_word, cfgtable)
    double_layered = identify_rows(col_poly, cfgtable['distance_th'], gcv_word, tabletitletext, cfgtable)
    df = layer_to_df(double_layered)
    log.info('finished parse_table')
    log.info(df)
    return df

def parse_tables_img(image, gcv_word, pagenum = None, model = None, cfg=cfg):
    """
    takes an image and gcv_word and turns it a data frame. 
    arguments: 

    image: ndarray of the page of the image
    gcv_word: google cloud vision word level 
    pagenum: optional, will allow calculation of pos_id and adding of coordinates to the dataframe 
    cfg: defaults to config.yml. This function specifically does not use anything from it, 
        but it is passed into functions parse_table and modeled_layout, which both use it extensively. 
    """
    l = [] # sets list for later use, will become a list of dataframes 
    table_layout, model = d2.modeled_layout(image, cfg=cfg, pagenum=pagenum, model=model) #uses function to get a modeled layout of the image using det2
    for i,x in enumerate(table_layout):
        df = parse_table(table_layout, gcv_word,i, image , cfgtable=cfg['Table']) # for each table in page, it passes it through parse_table to get dataframe
        df['x_1'] = x.coordinates[0] # gets coordinates of the table and adds them to dataframe 
        df['y_1'] = x.coordinates[1]
        df['x_2'] = x.coordinates[2]
        df['y_2'] = x.coordinates[3]
        if type(pagenum) == int: # If a pagenum was specified, it will calculate the position id for it and add it to df 
            log.info("pagenum {} was selected, calculating pos_id".format(pagenum))
            df['pagenum'] = pagenum
            df['pos_id'] = to_pos_id(
                y_1 = x.coordinates[1],
                y_2 = x.coordinates[3],
                pagenum = pagenum,
                docheight = image.shape[0] #calculates docheight using the height of the ndarray image 
            )
        if cfg['Table']['cont']['search']:
            df['cont'] = cont_or_not(x, gcv_word, cfgtable = cfg['Table'])
        l.append(df)
    df = pd.concat(l) # joins the data frames together into one, large data frame 
    df = df.reset_index() # rests the index so it is a normal index, leaves original index so you can tell the relative
#     position of each entry in reguards to its table
    if type(pagenum) == int: 
        df = df.sort_values(['pos_id', 'index']) # If pagenum specified, will sort the dataframes based on their relative pos_id in the larger df
    df = df.reset_index(drop=True)
    log.info('finished parse_tables_img')
    log.info(df)
    return df, model

def parse_page(pagenum, ocr_agent = None, overwrite = False, model = None, cfg=cfg):
    """
    At the moment, is just a simple wrapper for parse_tables_img to allow you to easily specify each individual page and pdf from config. 
    Will likely expand later. 
    """
    dir = '{}/{}/Parsed_Tables/'.format(cfg['OUTPUT_DIRECTORY'], cfg['SOURCE_NAME'])
    csv = dir + '{}.csv'.format(pagenum)
   #dir = '{}/{}/Parsed_Tables/{}.csv'.format(cfg['OUTPUT_DIRECTORY'], cfg['SOURCE_NAME'], pagenum)
    if not os.path.exists(dir): 
            os.makedirs(dir)
            log.warning("Directories not created for this project, created them.")
    if(os.path.isfile(csv)): 
        if overwrite == False: 
            log.info('File exists, returning from disk')
            return pd.read_csv(csv), ocr_agent, model
    file = "{}/{}".format(cfg['INPUT_DIRECTORY'], cfg['SOURCE_PDF']) # Gets file position from inut directory and name set in config file 
    image = convert_PDF(file, pagenum)
    res, ocr_agent = o.gcv_response(image,pagenum, ocr_agent=ocr_agent, cfg=cfg) # Gets GCV stuff. 
    gcv_block, gcv_para, gcv_word, gcv_char, ocr_agent = o.annotate_res(res, ocr_agent)
    df, model = parse_tables_img(image, gcv_word, pagenum, model=model, cfg=cfg) 
    df.to_csv(csv)
    log.info('Saved page {} to disk at {}'.format(pagenum, csv))
    return df, ocr_agent, model
    #return image, gcv_word
#def main():
def preload_gcv(pagenum, ocr_agent=None, cfg=cfg):
    file = "{}/{}".format(cfg['INPUT_DIRECTORY'], cfg['SOURCE_PDF']) # Gets file position from inut directory and name set in config file 
    image = convert_PDF(file, 1)
    res, ocr_agent = o.gcv_response(image, 1, ocr_agent=ocr_agent, cfg=cfg)
    for i in range(2,pagenum): 
        image = convert_PDF(file, i)
        log.info('converted page {}'.format(i))
        res = o.gcv_response(image, i, ocr_agent=ocr_agent, cfg=cfg)
        log.info('preloaded page {}'.format(i))

def multirun(start, end, overwrite=False):
    """
    Simple script for doing many pages at once
    """
    failed = []
    suceeded = []
    df, ocr_agent, model = parse_page(start, overwrite=overwrite)
    for x in range(start, end):
        try:
            df, ocr_agent, model = parse_page(x, ocr_agent=ocr_agent, model=model, overwrite=overwrite)
            log.info(df)
            suceeded.append(x)
        except:
            log.error("page {} failed".format(x))
            failed.append(x)
            pass    
    log.error(failed)
    log.info(suceeded)
if __name__ == '__main__':
    a = parse_page(785, overwrite=True)
  # pagenum = 88
  # file = "{}/{}".format(cfg["INPUT_DIRECTORY"], cfg["SOURCE_PDF"])
  # image = convert_PDF(file,pagenum)
  # res, ocr_agent = o.gcv_response(image,pagenum, cfg=cfg)  #
  # gcv_block, gcv_para, gcv_word, gcv_char, ocr_agent = o.annotate_res(res, ocr_agent)
  # a = d2.modeled_layout(image)
   # parse_table(d2.modeled_layout(image)[0], gcv_word, 1)

#   log.info('for debug only')
#   #main()
#   image = np.asarray(pdf2image.convert_from_path('/Users/liz/Documents/Projects/LayoutParser/test.pdf')[1])
#   table_layout = modeled_layout(image)
#   res, ocr_agent = gcv_response(image,1)
#   gcv_block, gcv_para, gcv_word, gcv_char = annotate_res(res, ocr_agent)
#   table_poly = to_polygons(table_layout)
#   table_txt = text_layout_from_selection(gcv_word, remove_titles(table_poly[0]))

#   #testing create polygons
#  # ll = create_bounding_polygons(remove_titles(table_poly[1]))
#  # hi = gcv_word.filter_by(ll[0], soft_margin = {"left":10, "right":10})
#  # lp.draw_box(image, ll, box_width=4).save("Tests/bruh3.png", "PNG")
#  # lp.draw_box(image, hi, box_width=4).save("Tests/bruh4.png", "PNG") 
#   table_title_1 = isolate_titles(table_poly[0])
#   tabletitletext = text_layout_from_selection(gcv_word, table_title_1)
#   
#   # %%
#   px = cols_px(tabletitletext)
#   l = remove_many_titles(table_poly)
#   a = column_poly(l[1], px, gcv_word)
#   lp.draw_box(image, a, box_width=4).save("Tests/21.png", "PNG")


# Change the method of finding nearest to use the average position not just y_1
# Find a better way of separating titles from rest of table thats not just based on hardcoded padding parameters. 

